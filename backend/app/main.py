import asyncio
import base64
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from app import auth, doctor, schedule
from app.agent import process_user_voice_message
from app.auth import current_user, require_role
from app.bookings import (
    cancel_appointment_as_patient,
    create_appointment,
    query_appointments,
    reschedule_appointment,
    send_due_reminders,
)
from app.db import connect, initialize_database, not_yet_started
from app.voice import LANGUAGE_PATTERN, text_to_speech_base64, transcribe_audio_bytes

app = FastAPI(
    title="MediVoc Memorial Hospital - Outpatient Appointment API",
    version="2.0.0",
    description="REST API for MediVoc Hospital outpatient scheduling, with role based access",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(schedule.router)
app.include_router(doctor.router)

patient_only = require_role("patient")
admin_only = require_role("admin")
staff_only = require_role("admin", "doctor")
# The voice desk serves two agents: the patient receptionist and the doctor assistant.
# app.agent picks the toolset from the signed-in role.
voice_users = require_role("patient", "doctor")


@app.on_event("startup")
async def on_startup() -> None:
    initialize_database()
    asyncio.create_task(_reminder_loop())


async def _reminder_loop() -> None:
    """Sends tomorrow's appointment reminders once an hour.

    `appointments.reminder_sent` makes the repeat harmless, which is why an hourly
    loop is enough and no scheduler dependency is needed.
    ponytail: the loop dies with the process, so a server restarted at the wrong hour
    delays a reminder. Move the call to the OS scheduler if that ever matters.
    """
    while True:
        try:
            result = await asyncio.to_thread(send_due_reminders)
            if result["reminders_sent"]:
                print(f"[Reminders] Sent {result['reminders_sent']} for {result['date']}.")
        except Exception as error:  # a failed round must never take the API down
            print(f"[Reminders] Skipped this round: {error}")
        await asyncio.sleep(3600)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "MediVoc Outpatient Desk API", "version": "2.0.0"}


# ============================== directory ==============================

@app.get("/doctors")
def list_doctors(department: Optional[str] = None) -> list[dict]:
    with connect() as connection:
        if department:
            rows = connection.execute(
                "SELECT * FROM doctors WHERE department = ? ORDER BY name", (department,)
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM doctors ORDER BY department, name").fetchall()
    return [dict(row) for row in rows]


@app.get("/slots")
def list_slots(
    doctor_id: Optional[int] = None,
    status: Optional[str] = "available",
    upcoming_only: bool = True,
) -> list[dict]:
    """Consultation calendar. Defaults to bookable (available, upcoming) slots."""
    query = "SELECT * FROM slots WHERE 1 = 1"
    params: list = []
    if doctor_id is not None:
        query += " AND doctor_id = ?"
        params.append(doctor_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    if upcoming_only:
        # Only slots that have not started yet, so this morning's clinic stops
        # appearing as bookable in the afternoon.
        clause, clause_params = not_yet_started()
        query += clause
        params.extend(clause_params)
    query += " ORDER BY date, start_time"

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@app.get("/holidays")
def public_holidays() -> list[dict]:
    """Upcoming hospital holidays, shown to patients on the booking page."""
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM holidays WHERE date >= ? ORDER BY date", (date.today().isoformat(),)
        ).fetchall()
    return [dict(row) for row in rows]


# ============================ appointments =============================

class AppointmentCreate(BaseModel):
    patient_name: str = Field(min_length=2, max_length=80)
    patient_age: int = Field(ge=1, le=120)
    patient_gender: str = Field(default="Not specified", max_length=20)
    patient_email: Optional[EmailStr] = None
    phone: str = Field(min_length=10, max_length=16)
    reason: str = Field(min_length=4, max_length=500)
    doctor_id: int
    slot_id: int


class RescheduleRequest(BaseModel):
    slot_id: int


@app.post("/appointments", status_code=201)
def book_appointment(payload: AppointmentCreate, user: dict = Depends(patient_only)) -> dict:
    """Form based booking. The confirmation email goes to the patient's account address."""
    return create_appointment(
        patient_id=user["id"],
        patient_name=payload.patient_name,
        patient_age=payload.patient_age,
        patient_gender=payload.patient_gender,
        patient_email=payload.patient_email or user["email"],
        phone=payload.phone,
        reason=payload.reason,
        doctor_id=payload.doctor_id,
        slot_id=payload.slot_id,
    )


@app.get("/me/appointments")
def my_appointments(user: dict = Depends(patient_only)) -> list[dict]:
    return query_appointments("appointments.patient_id = ?", (user["id"],))


@app.get("/appointments")
def appointment_ledger(user: dict = Depends(staff_only)) -> list[dict]:
    """Full OPD ledger. Admins see everything; a doctor sees only their own list."""
    if user["role"] == "doctor":
        return query_appointments("appointments.doctor_id = ?", (user["doctor_id"],))
    return query_appointments()


@app.delete("/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, user: dict = Depends(current_user)) -> dict:
    """A patient cancels their own booking; an admin can cancel any booking."""
    if user["role"] == "patient":
        return cancel_appointment_as_patient(appointment_id, patient_id=user["id"])
    if user["role"] == "admin":
        return cancel_appointment_as_patient(appointment_id)
    raise HTTPException(
        status_code=403,
        detail="Doctors must use the emergency cancellation endpoint so the patient is emailed.",
    )


@app.post("/appointments/{appointment_id}/reschedule")
def reschedule(
    appointment_id: int, payload: RescheduleRequest, user: dict = Depends(current_user)
) -> dict:
    if user["role"] == "patient":
        return reschedule_appointment(appointment_id, payload.slot_id, patient_id=user["id"])
    if user["role"] == "admin":
        # Only the desk may move a patient to a different specialist, and it is explicit.
        return reschedule_appointment(appointment_id, payload.slot_id, allow_doctor_change=True)
    raise HTTPException(status_code=403, detail="Only patients and admins can reschedule a booking.")


# ========================= voice ai agent routes ========================

class VoiceChatRequest(BaseModel):
    session_id: str = "default-session"
    message: str
    generate_audio: bool = True
    language: str = Field(default="en", pattern=LANGUAGE_PATTERN)


class VoiceAudioRequest(BaseModel):
    session_id: str = "default-session"
    audio_base64: str
    format: str = "webm"
    language: str = Field(default="en", pattern=LANGUAGE_PATTERN)


class VoiceTTSRequest(BaseModel):
    text: str
    language: str = Field(default="en", pattern=LANGUAGE_PATTERN)


@app.post("/voice/chat")
async def voice_chat(payload: VoiceChatRequest, user: dict = Depends(voice_users)) -> dict:
    """Text utterance -> LangGraph voice agent -> spoken reply.

    A patient reaches the receptionist agent, a doctor the staff desk agent.
    """
    result = await process_user_voice_message(
        payload.session_id, payload.message, patient=user, language=payload.language
    )
    audio_b64 = ""
    if payload.generate_audio and result.get("reply"):
        audio_b64 = await text_to_speech_base64(result["reply"], payload.language)
    return {
        "session_id": payload.session_id,
        "reply": result["reply"],
        "audio": audio_b64,
        "appointment": result.get("appointment"),
    }


@app.post("/voice/interact")
async def voice_interact(payload: VoiceAudioRequest, user: dict = Depends(voice_users)) -> dict:
    """Recorded audio -> local Whisper STT -> Gemini agent -> local TTS -> audio reply.

    Same role split as /voice/chat.
    """
    try:
        raw_b64 = payload.audio_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        audio_bytes = base64.b64decode(raw_b64)
        print(f"[STT] Received {len(audio_bytes)} bytes of audio (format={payload.format})")
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Invalid base64 audio data: {error}")

    try:
        user_text = transcribe_audio_bytes(
            audio_bytes, f".{payload.format}", language=payload.language
        )
        print(f"[STT Result] Transcribed text: '{user_text}'")
    except Exception as error:
        print(f"[STT Error] {error}")
        user_text = ""

    if not user_text.strip():
        fallback = "I could not detect any speech. Could you please speak again?"
        return {
            "session_id": payload.session_id,
            "user_text": "",
            "reply": fallback,
            "audio": await text_to_speech_base64(fallback, payload.language),
            "appointment": None,
        }

    result = await process_user_voice_message(
        payload.session_id, user_text, patient=user, language=payload.language
    )
    audio_b64 = ""
    if result.get("reply"):
        audio_b64 = await text_to_speech_base64(result["reply"], payload.language)

    return {
        "session_id": payload.session_id,
        "user_text": user_text,
        "reply": result["reply"],
        "audio": audio_b64,
        "appointment": result.get("appointment"),
    }


@app.post("/voice/transcribe")
async def voice_transcribe(
    file: UploadFile = File(...),
    language: str = Query(default="en", pattern=LANGUAGE_PATTERN),
    _: dict = Depends(voice_users),
) -> dict:
    """Transcribes an uploaded audio recording with local Whisper."""
    contents = await file.read()
    extension = Path(file.filename or "audio.webm").suffix or ".webm"
    return {"text": transcribe_audio_bytes(contents, extension, language=language)}


@app.post("/voice/synthesize")
async def voice_synthesize(payload: VoiceTTSRequest, _: dict = Depends(voice_users)) -> dict:
    """Synthesizes text into neural voice speech."""
    return {"audio": await text_to_speech_base64(payload.text, payload.language)}
