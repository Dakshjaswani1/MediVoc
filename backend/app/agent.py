"""Voice agents: a LangGraph ReAct loop over Gemini with hospital tools.

Two desks share this module. A patient gets the receptionist tools (book, cancel,
reschedule their own appointment); a doctor gets the staff tools (own schedule,
cancel a consultation, mark a day of leave). The toolset and the system prompt are
picked from the signed-in user's role, so a patient can never reach a doctor tool.
"""
import json
import os
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.bookings import (
    cancel_appointment_as_doctor,
    cancel_appointment_as_patient,
    create_appointment,
    query_appointments,
    reschedule_appointment,
)
from app.db import connect, not_yet_started
from app.doctor import list_doctor_slots
from app.schedule import record_leave

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

# The signed-in user (patient or doctor) and the appointment booked during this turn.
current_user_var: ContextVar[Optional[dict]] = ContextVar("current_user", default=None)
current_session_var: ContextVar[Optional[str]] = ContextVar("current_session", default=None)
current_language_var: ContextVar[str] = ContextVar("current_language", default="en")
session_appointments: dict[str, dict] = {}

# Languages the receptionist can hold a conversation in. The codes match
# app.voice.VOICES (Edge-TTS voice) and the Whisper language codes.
LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "ml": "Malayalam",
    "te": "Telugu",
    "kn": "Kannada",
}


def _tool_error(error: Exception) -> str:
    """Turns an HTTPException (or anything else) into a tool result the model can read out."""
    return json.dumps({"error": str(getattr(error, "detail", error))})


@tool
def get_departments() -> str:
    """Returns every clinical department available at MediVoc Hospital."""
    with connect() as connection:
        rows = connection.execute("SELECT DISTINCT department FROM doctors ORDER BY department").fetchall()
    return json.dumps({"departments": [row["department"] for row in rows]})


@tool
def get_doctors(department: Optional[str] = None) -> str:
    """Returns the specialist doctors, optionally filtered by department name."""
    with connect() as connection:
        if department:
            rows = connection.execute(
                "SELECT id, name, department, title, location, experience_years FROM doctors"
                " WHERE LOWER(department) LIKE ? ORDER BY name",
                (f"%{department.lower()}%",),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, name, department, title, location, experience_years FROM doctors"
                " ORDER BY department, name"
            ).fetchall()
    return json.dumps({"doctors": [dict(row) for row in rows]})


@tool
def get_available_slots(doctor_id: Optional[int] = None) -> str:
    """Returns upcoming free consultation slots, optionally for one doctor only."""
    query = """
        SELECT slots.id AS slot_id, slots.doctor_id, doctors.name AS doctor_name,
               doctors.department, slots.date, slots.start_time, slots.end_time
        FROM slots
        JOIN doctors ON doctors.id = slots.doctor_id
        WHERE slots.status = 'available' AND slots.date >= ?
    """
    params: list = [date.today().isoformat()]
    clause, clause_params = not_yet_started("slots.")
    query += clause
    params.extend(clause_params)
    if doctor_id is not None:
        query += " AND slots.doctor_id = ?"
        params.append(doctor_id)
    query += " ORDER BY slots.date, slots.start_time LIMIT 20"

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return json.dumps({"today": date.today().isoformat(), "available_slots": [dict(row) for row in rows]})


@tool
def get_my_appointments() -> str:
    """Returns the signed-in patient's own appointments, including cancelled ones."""
    patient = current_user_var.get()
    if not patient:
        return json.dumps({"error": "No patient is signed in."})
    appointments = query_appointments("appointments.patient_id = ?", (patient["id"],))
    return json.dumps(
        {
            "appointments": [
                {
                    "appointment_id": item["id"],
                    "reference_no": item["reference_no"],
                    "doctor_id": item["doctor_id"],
                    "doctor_name": item["doctor_name"],
                    "department": item["department"],
                    "date": item["date"],
                    "time": item["start_time"],
                    "status": item["status"],
                }
                for item in appointments
            ]
        }
    )


@tool
def book_appointment_tool(
    patient_age: int,
    patient_gender: str,
    reason: str,
    doctor_id: int,
    slot_id: int,
    phone: Optional[str] = None,
) -> str:
    """
    Books an outpatient consultation for the signed-in patient and emails the confirmation.

    - patient_age: age in years
    - patient_gender: 'Male', 'Female' or 'Other'
    - reason: chief complaint or symptoms
    - doctor_id: id from get_doctors
    - slot_id: id from get_available_slots
    - phone: only if the patient gives a different contact number
    """
    patient = current_user_var.get()
    if not patient:
        return json.dumps({"error": "No patient is signed in, so the booking cannot be saved."})

    try:
        appointment = create_appointment(
            patient_id=patient["id"],
            patient_name=patient["name"],
            patient_age=patient_age,
            patient_gender=patient_gender,
            patient_email=patient["email"],
            phone=(phone or patient.get("phone") or "0000000000"),
            reason=reason,
            doctor_id=doctor_id,
            slot_id=slot_id,
        )
    except Exception as error:  # surfaced back to the model as a normal tool result
        detail = getattr(error, "detail", str(error))
        return json.dumps({"error": str(detail)})

    session_id = current_session_var.get()
    if session_id:
        session_appointments[session_id] = appointment

    return json.dumps(
        {
            "status": "success",
            "token_id": appointment["reference_no"],
            "patient_name": appointment["patient_name"],
            "doctor_name": appointment["doctor_name"],
            "department": appointment["department"],
            "location": appointment["location"],
            "date": appointment["date"],
            "time": f"{appointment['start_time']} - {appointment['end_time']}",
            "email_sent_to": appointment["patient_email"],
        }
    )


@tool
def cancel_my_appointment(appointment_id: int) -> str:
    """
    Cancels one of the signed-in patient's own confirmed appointments and puts the slot
    back on sale. The patient also receives a cancellation email.

    - appointment_id: the appointment_id field returned by get_my_appointments

    Read the doctor, date and time back to the patient and get a clear yes before
    calling this. It cannot be undone; a cancelled appointment has to be booked again.
    """
    patient = current_user_var.get()
    if not patient:
        return json.dumps({"error": "No patient is signed in, so nothing can be cancelled."})

    try:
        result = cancel_appointment_as_patient(appointment_id, patient_id=patient["id"])
    except Exception as error:
        return _tool_error(error)

    appointment = result["appointment"]
    return json.dumps(
        {
            "status": "cancelled",
            "reference_no": appointment["reference_no"],
            "doctor_name": appointment["doctor_name"],
            "date": appointment["date"],
            "time": appointment["start_time"],
        }
    )


@tool
def reschedule_my_appointment(appointment_id: int, new_slot_id: int) -> str:
    """
    Moves one of the signed-in patient's appointments to another free slot, keeping the
    same token id. A new confirmation email is sent.

    - appointment_id: the appointment_id field returned by get_my_appointments
    - new_slot_id: a slot_id from get_available_slots for THAT SAME DOCTOR

    Call get_available_slots with the appointment's own doctor_id and offer only those
    times: a booking cannot be moved onto another doctor's slot and the attempt will be
    refused. If the patient wants a different specialist, book a new appointment instead.

    Use this for an appointment the hospital cancelled (status cancelled_by_doctor) as
    well as for one the patient simply wants to move. Confirm the new date and time with
    the patient before calling it.
    """
    patient = current_user_var.get()
    if not patient:
        return json.dumps({"error": "No patient is signed in, so nothing can be rescheduled."})

    try:
        result = reschedule_appointment(appointment_id, new_slot_id, patient_id=patient["id"])
    except Exception as error:
        return _tool_error(error)

    appointment = result["appointment"]
    session_id = current_session_var.get()
    if session_id:
        session_appointments[session_id] = appointment

    return json.dumps(
        {
            "status": "rescheduled",
            "reference_no": appointment["reference_no"],
            "doctor_name": appointment["doctor_name"],
            "department": appointment["department"],
            "location": appointment["location"],
            "date": appointment["date"],
            "time": f"{appointment['start_time']} - {appointment['end_time']}",
        }
    )


# ============================ doctor desk tools ============================
# These are only ever wired into the doctor agent, but each one re-checks the role
# as well: the tool is the trust boundary, not the toolset it happens to sit in.


def _signed_in_doctor() -> tuple[int | None, str | None]:
    """Returns (doctor_id, error_json). Exactly one of the two is set."""
    user = current_user_var.get()
    if not user:
        return None, json.dumps({"error": "No staff member is signed in."})
    if user.get("role") != "doctor":
        return None, json.dumps({"error": "This action is only available to a doctor."})
    if not user.get("doctor_id"):
        return None, json.dumps({"error": "This login is not linked to a doctor profile."})
    return user["doctor_id"], None


@tool
def doctor_get_my_schedule(day: Optional[str] = None, status: Optional[str] = None) -> str:
    """
    Returns the signed-in doctor's own upcoming consultation slots.

    - day: optional YYYY-MM-DD to look at a single date
    - status: optional 'available', 'booked' or 'blocked' filter

    Booked slots carry the patient's name, age, phone and reason, plus the
    appointment_id needed to cancel that consultation.
    """
    doctor_id, error = _signed_in_doctor()
    if error:
        return error

    slots = list_doctor_slots(doctor_id, status=status, day=day)[:30]
    return json.dumps(
        {
            "today": date.today().isoformat(),
            "slots": [
                {
                    "slot_id": row["id"],
                    "date": row["date"],
                    "time": f"{row['start_time']} - {row['end_time']}",
                    "status": row["status"],
                    "appointment_id": row["appointment_id"],
                    "patient_name": row["patient_name"],
                    "patient_age": row["patient_age"],
                    "phone": row["phone"],
                    "reason": row["reason"],
                    "appointment_status": row["appointment_status"],
                }
                for row in slots
            ],
        }
    )


@tool
def doctor_get_my_appointments() -> str:
    """Returns every appointment ever booked with the signed-in doctor, newest day first."""
    doctor_id, error = _signed_in_doctor()
    if error:
        return error

    appointments = query_appointments("appointments.doctor_id = ?", (doctor_id,))
    return json.dumps(
        {
            "today": date.today().isoformat(),
            "appointments": [
                {
                    "appointment_id": item["id"],
                    "reference_no": item["reference_no"],
                    "patient_name": item["patient_name"],
                    "patient_age": item["patient_age"],
                    "phone": item["phone"],
                    "reason": item["reason"],
                    "date": item["date"],
                    "time": item["start_time"],
                    "status": item["status"],
                }
                for item in appointments[:30]
            ],
        }
    )


@tool
def doctor_cancel_appointment(appointment_id: int, reason: str) -> str:
    """
    Emergency cancellation of one of the signed-in doctor's own booked consultations.
    The patient is emailed to reschedule and the slot is blocked, not resold.

    - appointment_id: from doctor_get_my_schedule or doctor_get_my_appointments
    - reason: short explanation that goes into the patient's email

    Read the patient name, date and time back to the doctor and wait for a clear yes
    before calling this. It cannot be undone.
    """
    doctor_id, error = _signed_in_doctor()
    if error:
        return error

    try:
        result = cancel_appointment_as_doctor(appointment_id, reason, doctor_id=doctor_id)
    except Exception as failure:
        return _tool_error(failure)

    appointment = result["appointment"]
    return json.dumps(
        {
            "status": "cancelled_by_doctor",
            "reference_no": appointment["reference_no"],
            "patient_name": appointment["patient_name"],
            "date": appointment["date"],
            "time": appointment["start_time"],
            "email_sent": result["email_sent"],
        }
    )


@tool
def doctor_mark_leave(leave_date: str, reason: Optional[str] = None) -> str:
    """
    Books the signed-in doctor off for one whole day.

    - leave_date: the day off as YYYY-MM-DD. Work this out from today's date yourself;
      never pass a weekday name or a phrase like 'tomorrow'.
    - reason: short reason, read out to any patient who has to be cancelled

    Free slots on that day are removed and every booked consultation is cancelled with
    an email to the patient. Confirm the exact date and how many patients are affected
    with the doctor before calling this, and never call it for a past date.
    """
    doctor_id, error = _signed_in_doctor()
    if error:
        return error

    try:
        result = record_leave(doctor_id, leave_date, reason or "")
    except Exception as failure:
        return _tool_error(failure)

    return json.dumps(
        {
            "status": "leave_recorded",
            "date": result["date"],
            "reason": result["reason"],
            "cancelled_appointments": result["cancelled_appointments"],
        }
    )


PATIENT_SYSTEM_PROMPT = """You are MediVoc Memorial Hospital's AI Voice Receptionist.
You help a signed-in patient book, move or cancel an outpatient doctor consultation over a
natural voice conversation.

PATIENT ON THE LINE:
{patient_block}

LANGUAGE:
Speak and reply ONLY in {language_name}. Keep doctor names, department names and the token
id in their original form so the patient can repeat them at the reception desk. If the patient clearly
switches into English mid-call, follow them. Do not reply in any other language.

IMPORTANT VOICE CONVERSATION RULES:
1. Speak in a warm, empathetic and professional medical tone.
2. Keep answers brief and natural for text-to-speech. Never output markdown asterisks,
   hashes, bullet symbols or tables. Use plain conversational sentences.
3. The patient is already signed in, so never ask for their name or email address.
4. To BOOK a new appointment:
   a. Ask about symptoms or which specialty they need (General Medicine, Cardiology,
      Dermatology, Orthopedics, Pediatrics).
   b. Use get_doctors to find a matching physician and recommend one with title and department.
   c. Use get_available_slots for that doctor and offer one or two specific times,
      for example "I have Monday the 11th at 9:30 in the morning, or 10:10".
   d. Collect only what is missing: age, gender, and the reason for the visit.
   e. Call book_appointment_tool once you have the slot id and those details.
   f. After a successful booking, read out the token id, doctor name, room location, date
      and time, and mention that a confirmation email has been sent. Remind them to arrive
      15 minutes early.
5. To CANCEL an appointment:
   a. Call get_my_appointments and find the one they mean. If more than one could match,
      ask which doctor or date they mean instead of guessing.
   b. Read the doctor, date and time back and wait for a clear yes.
   c. Then call cancel_my_appointment with that appointment_id, and confirm that the slot
      has been released and a cancellation email sent.
6. To RESCHEDULE or move an appointment:
   a. Call get_my_appointments to get the appointment_id and the current doctor.
   b. Call get_available_slots for that doctor and offer one or two specific free times.
   c. Call reschedule_my_appointment with the appointment_id and the new slot id.
   d. Tell them the token id stays the same and read out the new date and time.
   e. If an appointment shows status cancelled_by_doctor, the hospital cancelled it:
      apologise briefly and offer to reschedule it straight away.
7. Never cancel or move an appointment the patient did not ask you to touch, and never
   cancel when they only asked to move one.
8. If a slot was just taken, apologise briefly and offer the next free time.
9. Today's date is {today}.
"""

DOCTOR_SYSTEM_PROMPT = """You are MediVoc Memorial Hospital's AI Desk Assistant for doctors.
You help the signed-in doctor review their own clinic, cancel a consultation they cannot
attend, and mark a day of leave, over a natural voice conversation.

DOCTOR ON THE LINE:
{patient_block}

LANGUAGE:
Speak and reply ONLY in {language_name}. Keep patient names, department names and token
ids in their original form. Do not reply in any other language.

IMPORTANT VOICE CONVERSATION RULES:
1. Speak briefly, calmly and professionally, like a competent desk assistant to a busy
   clinician. Never output markdown asterisks, hashes, bullet symbols or tables; use
   plain conversational sentences a text-to-speech voice can read.
2. You are talking to the doctor, not to a patient. Never offer to book an appointment,
   and never discuss another doctor's patients: every tool is scoped to this doctor.
3. To REVIEW the clinic:
   a. Use doctor_get_my_schedule for slots, optionally for one date or one status.
      Summarise out loud, for example "Tomorrow you have six booked and two free".
   b. Only read out individual patient details the doctor actually asks for.
   c. Use doctor_get_my_appointments for the longer history, including cancelled ones
      and consultations already closed as attended or no-show.
4. To CANCEL a consultation:
   a. Find it with doctor_get_my_schedule or doctor_get_my_appointments.
   b. If more than one could match, ask which patient or time they mean. Never guess.
   c. Read the patient name, date and time back and wait for a clear yes.
   d. Ask for a short reason if the doctor has not given one, because it goes into the
      patient's email.
   e. Then call doctor_cancel_appointment, and confirm the patient has been emailed to
      reschedule and that the slot is blocked rather than resold.
5. To MARK LEAVE for a whole day:
   a. Work out the exact calendar date yourself from today's date and say it back, for
      example "That is Thursday the 26th of November".
   b. Warn the doctor first if that day has booked consultations: check with
      doctor_get_my_schedule for that date and say how many patients will be cancelled.
   c. Wait for a clear yes, then call doctor_mark_leave with the date in YYYY-MM-DD form.
   d. Report how many appointments were cancelled and that those patients were emailed.
   e. Leave cannot be undone by voice. Tell the doctor to ask the administrator if they
      need the day put back.
6. Never cancel a consultation or mark leave the doctor did not ask for, and never do
   either one just because they asked a question about the schedule.
7. If a tool returns an error, say plainly what went wrong and what you need instead.
8. Today's date is {today}.
"""

_PATIENT_TOOLS = [
    get_departments,
    get_doctors,
    get_available_slots,
    get_my_appointments,
    book_appointment_tool,
    cancel_my_appointment,
    reschedule_my_appointment,
]

_DOCTOR_TOOLS = [
    doctor_get_my_schedule,
    doctor_get_my_appointments,
    doctor_cancel_appointment,
    doctor_mark_leave,
]

# Per role: the tools it may call and the prompt it runs under. A role that is not in
# here (admin, for now) has no voice desk at all.
DESKS = {
    "patient": (_PATIENT_TOOLS, PATIENT_SYSTEM_PROMPT),
    "doctor": (_DOCTOR_TOOLS, DOCTOR_SYSTEM_PROMPT),
}

_checkpointer = MemorySaver()
_agent_graphs: dict[str, object] = {}


def _api_key() -> Optional[str]:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def get_agent(role: str = "patient"):
    """Builds one ReAct agent per role and reuses it."""
    if role not in DESKS:
        return None
    if role in _agent_graphs:
        return _agent_graphs[role]

    api_key = _api_key()
    if not api_key:
        return None

    llm = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        google_api_key=api_key,
        temperature=0.3,
    )
    _agent_graphs[role] = create_react_agent(
        model=llm,
        tools=DESKS[role][0],
        # The prompt is a callable so each turn carries the current user's details.
        prompt=_build_prompt,
        checkpointer=_checkpointer,
    )
    return _agent_graphs[role]


def _build_prompt(state: dict) -> list:
    from langchain_core.messages import SystemMessage

    user = current_user_var.get() or {}
    _, template = DESKS.get(user.get("role", "patient"), DESKS["patient"])
    user_block = (
        f"Name: {user.get('name', 'Unknown')}\n"
        f"Email: {user.get('email', 'unknown')}\n"
        f"Phone: {user.get('phone') or 'not on file'}"
    )
    language = current_language_var.get()
    system = template.format(
        patient_block=user_block,
        language_name=LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["en"]),
        today=date.today().isoformat(),
    )
    return [SystemMessage(content=system)] + list(state["messages"])


async def process_user_voice_message(
    session_id: str,
    message: str,
    patient: Optional[dict] = None,
    language: str = "en",
) -> dict:
    """Runs one utterance from a signed-in patient or doctor through their own agent.

    Returns {"reply": str, "appointment": dict | None}.
    """
    role = (patient or {}).get("role", "patient")
    if role not in DESKS:
        return {
            "reply": "The voice desk is only available to patients and doctors.",
            "appointment": None,
        }

    if not _api_key():
        return {
            "reply": "Welcome to MediVoc Memorial Hospital. Please add your GEMINI_API_KEY to the"
            " backend .env file to enable the voice booking agent.",
            "appointment": None,
        }

    agent = get_agent(role)
    if agent is None:
        return {
            "reply": "Unable to initialise the Gemini voice agent. Please verify your GEMINI_API_KEY.",
            "appointment": None,
        }

    session_appointments.pop(session_id, None)
    session_token = current_session_var.set(session_id)
    patient_token = current_user_var.set(patient)
    language_token = current_language_var.set(language if language in LANGUAGE_NAMES else "en")

    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": session_id}},
        )

        reply_text = ""
        for msg in reversed(result.get("messages", [])):
            if msg.type == "ai" and msg.content:
                if isinstance(msg.content, str):
                    reply_text = msg.content
                else:
                    reply_text = " ".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in msg.content
                    )
                break

        return {
            "reply": reply_text or "How can I assist you with your appointment today?",
            "appointment": session_appointments.get(session_id),
        }
    except Exception as error:
        print(f"[Agent Error] {error}")
        return {
            "reply": f"I had a temporary issue processing that request: {error}. Please try again.",
            "appointment": None,
        }
    finally:
        current_session_var.reset(session_token)
        current_user_var.reset(patient_token)
        current_language_var.reset(language_token)
