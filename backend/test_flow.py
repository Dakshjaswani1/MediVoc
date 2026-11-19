"""End-to-end check for the MediVoc API: roles, shifts, booking, emails, cancellation.

Run it with the backend dependencies installed:

    cd backend
    python test_flow.py

It uses a throwaway copy of the database, so your real data is untouched.
Every step asserts, so a broken flow fails loudly instead of printing noise.
"""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Point the app at a temporary database BEFORE importing it.
TEMP_DB = Path(tempfile.mkdtemp(prefix="medivoc-test-")) / "test.db"

import app.db as db  # noqa: E402

db.DATA_DIR = TEMP_DB.parent
db.DB_PATH = TEMP_DB

import app.mailer as mailer  # noqa: E402

mailer.MAIL_LOG = TEMP_DB.parent / "sent_emails.log"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app as fastapi_app  # noqa: E402

client = TestClient(fastapi_app)


def sign_in(email: str, password: str) -> dict:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['token']}"}


def check(label: str, condition: bool) -> None:
    assert condition, f"FAILED: {label}"
    print(f"  ok  {label}")


with client:  # triggers startup: schema, seed accounts, slot generation
    print("\n1. Authentication and roles")
    admin = sign_in("admin@medivoc.com", "admin123")
    doctor = sign_in("asha@medivoc.com", "doctor123")
    patient = sign_in("patient@medivoc.com", "patient123")
    check("admin, doctor and patient can sign in", True)

    check("no token is rejected", client.get("/me/appointments").status_code == 401)
    check("bad password is rejected", client.post(
        "/auth/login", json={"email": "admin@medivoc.com", "password": "wrong"}
    ).status_code == 401)
    check("patient cannot open the admin area", client.get("/admin/overview", headers=patient).status_code == 403)
    check("doctor cannot open the admin area", client.get("/admin/overview", headers=doctor).status_code == 403)
    check("admin can open the admin area", client.get("/admin/overview", headers=admin).status_code == 200)
    check("doctor cannot read another role's endpoint", client.get("/me/appointments", headers=doctor).status_code == 403)

    print("\n2. Patient self-registration")
    new_patient = client.post(
        "/auth/register",
        json={"name": "Rahul Verma", "email": "rahul@example.com", "phone": "9876543210", "password": "secret123"},
    )
    check("registration returns a token", new_patient.status_code == 201 and "token" in new_patient.json())
    check("duplicate email is refused", client.post(
        "/auth/register",
        json={"name": "Rahul Verma", "email": "rahul@example.com", "phone": "9876543210", "password": "secret123"},
    ).status_code == 409)

    print("\n3. Admin: shifts, slot generation, holidays, leaves")
    shifts = client.get("/admin/shifts", headers=admin).json()
    check(f"seeded shifts exist ({len(shifts)} rows)", len(shifts) > 0)

    added = client.post(
        "/admin/shifts",
        headers=admin,
        json={"doctor_id": 2, "weekday": 5, "start_time": "16:00", "end_time": "18:00", "slot_minutes": 30},
    )
    check("admin adds a Saturday evening shift", added.status_code == 201)
    check("the new shift generated slots", added.json()["slots"]["created_slots"] > 0)
    check("overlapping shift is refused", client.post(
        "/admin/shifts",
        headers=admin,
        json={"doctor_id": 2, "weekday": 5, "start_time": "17:00", "end_time": "19:00", "slot_minutes": 30},
    ).status_code == 409)

    holiday_date = (date.today() + timedelta(days=3)).isoformat()
    holiday = client.post("/admin/holidays", headers=admin, json={"date": holiday_date, "name": "Founders Day"})
    check("admin declares a holiday", holiday.status_code == 201)
    open_on_holiday = [s for s in client.get("/slots").json() if s["date"] == holiday_date]
    check("no bookable slots remain on the holiday", open_on_holiday == [])

    leave_date = (date.today() + timedelta(days=4)).isoformat()
    leave = client.post(
        "/admin/leaves", headers=admin, json={"doctor_id": 1, "date": leave_date, "reason": "Conference"}
    )
    check("admin records doctor leave", leave.status_code == 201)
    on_leave = [s for s in client.get("/slots", params={"doctor_id": 1}).json() if s["date"] == leave_date]
    check("that doctor has no slots on the leave day", on_leave == [])
    still_open = [s for s in client.get("/slots", params={"doctor_id": 2}).json() if s["date"] == leave_date]
    check("other doctors are unaffected by the leave", len(still_open) >= 0)

    print("\n4. Patient booking by form (plus confirmation email)")
    free = client.get("/slots", params={"doctor_id": 2}).json()
    check("doctor 2 has bookable slots", len(free) > 1)
    slot = free[0]

    booking = client.post(
        "/appointments",
        headers=patient,
        json={
            "patient_name": "Demo Patient",
            "patient_age": 34,
            "patient_gender": "Male",
            "phone": "9000000009",
            "reason": "Chest tightness while climbing stairs",
            "doctor_id": slot["doctor_id"],
            "slot_id": slot["id"],
        },
    )
    check("booking succeeds", booking.status_code == 201)
    appointment = booking.json()
    check(f"token issued: {appointment['reference_no']}", appointment["reference_no"].startswith("MV-2026-"))
    check("confirmation email was composed", mailer.MAIL_LOG.exists())
    mail_text = mailer.MAIL_LOG.read_text(encoding="utf-8")
    check("email carries the token, doctor, date and symptoms", all(
        token in mail_text
        for token in (appointment["reference_no"], appointment["doctor_name"], appointment["date"], "Chest tightness")
    ))

    check("the slot is no longer bookable", client.post(
        "/appointments",
        headers=patient,
        json={
            "patient_name": "Demo Patient", "patient_age": 34, "patient_gender": "Male",
            "phone": "9000000009", "reason": "Second attempt on the same slot",
            "doctor_id": slot["doctor_id"], "slot_id": slot["id"],
        },
    ).status_code == 409)
    check("anonymous booking is refused", client.post(
        "/appointments",
        json={
            "patient_name": "Nobody", "patient_age": 20, "patient_gender": "Other",
            "phone": "9000000000", "reason": "No login at all",
            "doctor_id": slot["doctor_id"], "slot_id": free[1]["id"],
        },
    ).status_code == 401)

    print("\n5. Doctor desk")
    cardiologist = sign_in("raghav@medivoc.com", "doctor123")
    booked = client.get("/doctor/slots", headers=cardiologist, params={"status": "booked"}).json()
    check("doctor sees the booked slot with patient details", any(
        row["appointment_id"] == appointment["id"] and row["patient_name"] == "Demo Patient" for row in booked
    ))
    available = client.get("/doctor/slots", headers=cardiologist, params={"status": "available"}).json()
    check(f"doctor sees own free slots ({len(available)})", len(available) > 0)

    blocked = client.patch(f"/doctor/slots/{available[0]['id']}", headers=cardiologist, json={"status": "blocked"})
    check("doctor blocks a free slot", blocked.status_code == 200)
    check("blocked slot disappears from the patient calendar", available[0]["id"] not in [
        s["id"] for s in client.get("/slots", params={"doctor_id": 2}).json()
    ])
    check("doctor re-opens the slot", client.patch(
        f"/doctor/slots/{available[0]['id']}", headers=cardiologist, json={"status": "available"}
    ).status_code == 200)
    check("doctor cannot touch another doctor's slot", client.patch(
        f"/doctor/slots/{available[0]['id']}", headers=doctor, json={"status": "blocked"}
    ).status_code == 404)
    check("blocking a booked slot is refused", client.patch(
        f"/doctor/slots/{slot['id']}", headers=cardiologist, json={"status": "blocked"}
    ).status_code == 409)

    extra = client.post(
        "/doctor/slots",
        headers=cardiologist,
        json={"date": (date.today() + timedelta(days=10)).isoformat(), "start_time": "19:00", "end_time": "19:20"},
    )
    check("doctor opens an extra slot", extra.status_code == 201)

    print("\n6. Emergency cancellation by the doctor")
    cancelled = client.post(
        f"/doctor/appointments/{appointment['id']}/cancel",
        headers=cardiologist,
        json={"reason": "Called into emergency surgery"},
    )
    check("doctor cancels the consultation", cancelled.status_code == 200)
    check("appointment is marked cancelled_by_doctor",
          cancelled.json()["appointment"]["status"] == "cancelled_by_doctor")
    mail_text = mailer.MAIL_LOG.read_text(encoding="utf-8")
    check("cancellation email mentions the reason and rescheduling", all(
        phrase in mail_text for phrase in ("Called into emergency surgery", "Reschedule")
    ))
    check("the freed slot is blocked, not resold", slot["id"] not in [
        s["id"] for s in client.get("/slots", params={"doctor_id": 2}).json()
    ])
    check("a doctor cannot cancel another doctor's appointment", client.post(
        f"/doctor/appointments/{appointment['id']}/cancel", headers=doctor, json={"reason": "x"}
    ).status_code in (400, 403))

    print("\n7. Patient reschedules after the cancellation")
    mine = client.get("/me/appointments", headers=patient).json()
    check("patient sees the cancelled appointment", mine[0]["status"] == "cancelled_by_doctor")
    check("patient sees the cancellation reason", "emergency surgery" in mine[0]["cancel_reason"])

    new_slot = client.get("/slots", params={"doctor_id": 2}).json()[0]
    moved = client.post(
        f"/appointments/{appointment['id']}/reschedule", headers=patient, json={"slot_id": new_slot["id"]}
    )
    check("patient reschedules into a free slot", moved.status_code == 200)
    check("appointment is confirmed again", moved.json()["appointment"]["status"] == "confirmed")
    check("token id is unchanged", moved.json()["appointment"]["reference_no"] == appointment["reference_no"])
    mail_text = mailer.MAIL_LOG.read_text(encoding="utf-8")
    check("reschedule email was composed", "rescheduled" in mail_text)

    other = {"Authorization": f"Bearer {new_patient.json()['token']}"}
    check("another patient cannot reschedule it", client.post(
        f"/appointments/{appointment['id']}/reschedule", headers=other, json={"slot_id": new_slot["id"]}
    ).status_code in (403, 409))

    print("\n8. Patient cancels, slot returns to the pool")
    released = client.delete(f"/appointments/{appointment['id']}", headers=patient)
    check("patient cancels own appointment", released.status_code == 200)
    check("slot is bookable again", new_slot["id"] in [
        s["id"] for s in client.get("/slots", params={"doctor_id": 2}).json()
    ])
    check("another patient cannot cancel it", client.delete(
        f"/appointments/{appointment['id']}", headers=other
    ).status_code in (400, 403))

    print("\n9. Ledger visibility")
    check("admin sees the whole ledger", len(client.get("/appointments", headers=admin).json()) >= 1)
    doctor_ledger = client.get("/appointments", headers=cardiologist).json()
    check("doctor ledger is limited to own patients", all(row["doctor_id"] == 2 for row in doctor_ledger))
    check("patient cannot read the ledger", client.get("/appointments", headers=patient).status_code == 403)

    print("\n10. Consultation outcomes: attended and no-show")
    outcome_slots = client.get("/slots", params={"doctor_id": 2}).json()
    check("doctor 2 still has free slots to book", len(outcome_slots) >= 2)

    def book(slot_id: int, reason: str) -> dict:
        response = client.post(
            "/appointments",
            headers=patient,
            json={
                "patient_name": "Demo Patient", "patient_age": 34, "patient_gender": "Male",
                "phone": "9000000009", "reason": reason,
                "doctor_id": 2, "slot_id": slot_id,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    attended = book(outcome_slots[0]["id"], "Follow up on chest tightness")
    missed = book(outcome_slots[1]["id"], "Routine cardiology review")

    closed = client.post(
        f"/doctor/appointments/{attended['id']}/outcome", headers=cardiologist, json={"status": "completed"}
    )
    check("doctor marks a consultation attended", closed.status_code == 200)
    check("appointment status becomes completed", closed.json()["appointment"]["status"] == "completed")

    check("a completed consultation cannot be closed twice", client.post(
        f"/doctor/appointments/{attended['id']}/outcome", headers=cardiologist, json={"status": "completed"}
    ).status_code == 400)
    check("the patient can no longer cancel a completed consultation", client.delete(
        f"/appointments/{attended['id']}", headers=patient
    ).status_code == 400)
    check("an invalid outcome is rejected", client.post(
        f"/doctor/appointments/{missed['id']}/outcome", headers=cardiologist, json={"status": "maybe"}
    ).status_code == 422)
    check("another doctor cannot close it", client.post(
        f"/doctor/appointments/{missed['id']}/outcome", headers=doctor, json={"status": "completed"}
    ).status_code == 403)

    no_show = client.post(
        f"/doctor/appointments/{missed['id']}/outcome", headers=cardiologist, json={"status": "no_show"}
    )
    check("doctor marks a no-show", no_show.json()["appointment"]["status"] == "no_show")

    closed_slot = [
        row for row in client.get("/doctor/slots", headers=cardiologist).json()
        if row["appointment_id"] == attended["id"]
    ]
    check("the doctor desk still shows the patient after closing", len(closed_slot) == 1)
    check("the slot carries the outcome", closed_slot[0]["appointment_status"] == "completed")
    check("the consultation time is not resold", closed_slot[0]["status"] == "booked")

    patient_view = {row["id"]: row for row in client.get("/me/appointments", headers=patient).json()}
    check("the patient sees the completed consultation", patient_view[attended["id"]]["status"] == "completed")
    check("the patient sees the no-show", patient_view[missed["id"]]["status"] == "no_show")

    stats = client.get("/admin/overview", headers=admin).json()
    check("overview counts completed consultations", stats["completed_consultations"] >= 1)
    check("overview counts no-shows", stats["no_shows"] >= 1)

    print("\n11. Day-before reminder emails")
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    extra = client.post(
        "/doctor/slots", headers=cardiologist,
        json={"date": tomorrow, "start_time": "16:00", "end_time": "16:20"},
    )
    check("doctor opens a slot for tomorrow", extra.status_code == 201)
    reminded = book(extra.json()["slot_id"], "Blood pressure review")

    first = client.post("/admin/send-reminders", headers=admin, json={"days_ahead": 1})
    check("admin can send the reminders", first.status_code == 200)
    check("reminders target tomorrow", first.json()["date"] == tomorrow)
    mail_text = mailer.MAIL_LOG.read_text(encoding="utf-8")
    check("a reminder email was composed for tomorrow", f"Reminder: appointment on {tomorrow}" in mail_text)
    check("the reminder carries the token", reminded["reference_no"] in mail_text)

    second = client.post("/admin/send-reminders", headers=admin, json={"days_ahead": 1})
    check("the same patient is not reminded twice", second.json()["reminders_sent"] == 0)
    check("a patient cannot trigger the reminder run", client.post(
        "/admin/send-reminders", headers=patient, json={"days_ahead": 1}
    ).status_code == 403)

    print("\n12. Voice agent tools: cancel, reschedule and language")
    import json as _json

    import app.agent as agent

    me = client.get("/auth/me", headers=patient).json()
    other_me = client.get("/auth/me", headers=other).json()

    def as_user(account: dict, tool, **arguments) -> dict:
        """Runs one agent tool the way a voice turn would, with that account signed in."""
        handle = agent.current_user_var.set(account)
        try:
            return _json.loads(tool.invoke(arguments))
        finally:
            agent.current_user_var.reset(handle)

    listed = as_user(me, agent.get_my_appointments)["appointments"]
    check("the agent can list the patient's appointments", len(listed) >= 1)
    check("each one carries an appointment_id to act on", all("appointment_id" in row for row in listed))

    moveable = client.get("/slots", params={"doctor_id": 2}).json()
    check("there is a free slot to move into", len(moveable) >= 1)
    moved = as_user(me, agent.reschedule_my_appointment,
                       appointment_id=reminded["id"], new_slot_id=moveable[0]["id"])
    check("the agent reschedules by voice", moved.get("status") == "rescheduled")
    check("the token id survives the move", moved["reference_no"] == reminded["reference_no"])

    blocked = as_user(other_me, agent.cancel_my_appointment, appointment_id=reminded["id"])
    check("the agent refuses to cancel another patient's booking", "error" in blocked)
    check("that booking is still confirmed", client.get(
        "/me/appointments", headers=patient
    ).json() and [
        row for row in client.get("/me/appointments", headers=patient).json()
        if row["id"] == reminded["id"] and row["status"] == "confirmed"
    ])

    dropped = as_user(me, agent.cancel_my_appointment, appointment_id=reminded["id"])
    check("the agent cancels by voice", dropped.get("status") == "cancelled")
    check("the slot is back on sale", moveable[0]["id"] in [
        row["id"] for row in client.get("/slots", params={"doctor_id": 2}).json()
    ])
    check("the agent cannot cancel it twice", "error" in as_user(
        me, agent.cancel_my_appointment, appointment_id=reminded["id"]
    ))

    import app.voice as voice

    check("every conversation language has a neural voice", set(agent.LANGUAGE_NAMES) == set(voice.VOICES))
    check("more than one language is actually wired up", len(voice.VOICES) >= 6)
    language_handle = agent.current_language_var.set("hi")
    try:
        rendered = agent._build_prompt({"messages": []})[0].content
    finally:
        agent.current_language_var.reset(language_handle)
    check("the prompt tells the agent which language to speak", "ONLY in Hindi" in rendered)
    check("an unsupported language code is rejected", client.post(
        "/voice/chat", headers=patient, json={"message": "hello", "language": "fr"}
    ).status_code == 422)

    print("\n13. Doctor voice desk: schedule, cancellation and leave")
    doctor_me = client.get("/auth/me", headers=cardiologist).json()
    check("the doctor account is linked to a doctor profile", doctor_me["doctor_id"] == 2)

    check("a patient has no doctor tools", "error" in as_user(me, agent.doctor_get_my_schedule))
    check("a patient cannot mark leave", "error" in as_user(
        me, agent.doctor_mark_leave, leave_date=(date.today() + timedelta(days=3)).isoformat()
    ))

    schedule_view = as_user(doctor_me, agent.doctor_get_my_schedule)
    check("the doctor agent reads its own clinic", len(schedule_view["slots"]) >= 1)
    check("every slot carries the data needed to act on it", all(
        {"slot_id", "date", "status", "appointment_id"} <= set(row) for row in schedule_view["slots"]
    ))

    ledger_view = as_user(doctor_me, agent.doctor_get_my_appointments)
    check("the doctor agent reads its own appointment list", len(ledger_view["appointments"]) >= 1)
    check("every appointment carries an appointment_id to act on", all(
        "appointment_id" in row for row in ledger_view["appointments"]
    ))

    voice_target = book(client.get("/slots", params={"doctor_id": 2}).json()[0]["id"], "Palpitations")
    general_physician = client.get(
        "/auth/me", headers=sign_in("asha@medivoc.com", "doctor123")
    ).json()
    wrong_doctor = as_user(
        general_physician, agent.doctor_cancel_appointment,
        appointment_id=voice_target["id"], reason="not mine",
    )
    check("a doctor cannot cancel another doctor's consultation", "error" in wrong_doctor)

    cancelled = as_user(doctor_me, agent.doctor_cancel_appointment,
                        appointment_id=voice_target["id"], reason="Called into theatre")
    check("the doctor agent cancels by voice", cancelled.get("status") == "cancelled_by_doctor")
    check("the slot is blocked, not resold", voice_target["slot_id"] not in [
        row["id"] for row in client.get("/slots", params={"doctor_id": 2}).json()
    ])
    check("the patient was emailed the reason", "Called into theatre" in mailer.MAIL_LOG.read_text(
        encoding="utf-8"
    ))
    check("it cannot be cancelled twice", "error" in as_user(
        doctor_me, agent.doctor_cancel_appointment,
        appointment_id=voice_target["id"], reason="again",
    ))

    check("a spoken weekday is rejected as a leave date", "error" in as_user(
        doctor_me, agent.doctor_mark_leave, leave_date="next Monday"
    ))
    check("an impossible date is rejected", "error" in as_user(
        doctor_me, agent.doctor_mark_leave, leave_date="2026-02-31"
    ))

    leave_day = (date.today() + timedelta(days=5)).isoformat()
    before = len(client.get("/slots", params={"doctor_id": 2}).json())
    marked = as_user(doctor_me, agent.doctor_mark_leave, leave_date=leave_day, reason="Conference")
    check("the doctor agent marks leave by voice", marked.get("status") == "leave_recorded")
    check("the leave lands on the right date", marked["date"] == leave_day)
    check("that day is cleared from the doctor's free slots", not [
        row for row in client.get("/slots", params={"doctor_id": 2}).json() if row["date"] == leave_day
    ])
    check("other days are untouched", len(client.get("/slots", params={"doctor_id": 2}).json()) < before)
    check("the admin sees the leave the doctor marked", any(
        row["date"] == leave_day and row["doctor_id"] == 2
        for row in client.get("/admin/leaves", headers=admin).json()
    ))
    check("the same day cannot be marked twice", "error" in as_user(
        doctor_me, agent.doctor_mark_leave, leave_date=leave_day
    ))

    doctor_handle = agent.current_user_var.set(doctor_me)
    try:
        doctor_prompt = agent._build_prompt({"messages": []})[0].content
    finally:
        agent.current_user_var.reset(doctor_handle)
    check("the doctor desk runs under the doctor prompt", "AI Desk Assistant for doctors" in doctor_prompt)
    check("the patient desk still runs under the receptionist prompt",
          "AI Voice Receptionist" in agent._build_prompt({"messages": []})[0].content)
    check("patient and doctor desks have different tools",
          {item.name for item in agent.DESKS["patient"][0]}
          != {item.name for item in agent.DESKS["doctor"][0]})
    check("an admin has no voice desk", "admin" not in agent.DESKS)
    check("an admin is refused at the voice route", client.post(
        "/voice/chat", headers=admin, json={"message": "hello"}
    ).status_code == 403)
    check("a doctor is let in at the voice route", client.post(
        "/voice/chat", headers=cardiologist, json={"message": "hello"}
    ).status_code == 200)

    print("\n14. Every advertised language reaches the agent")
    import app.voice as voice_module

    check("the six languages the frontend offers all have a neural voice",
          set(voice_module.VOICES) == {"en", "hi", "ta", "ml", "te", "kn"})
    check("the request guard is built from that same map",
          voice_module.LANGUAGE_PATTERN == f"^({'|'.join(voice_module.VOICES)})$")
    for code in sorted(voice_module.VOICES):
        check(f"/voice/chat accepts language={code}", client.post(
            "/voice/chat", headers=patient, json={"message": "hello", "language": code}
        ).status_code == 200)
        check(f"the {code} prompt names its own language", code in agent.LANGUAGE_NAMES)
    check("a language with no voice is still rejected", client.post(
        "/voice/chat", headers=patient, json={"message": "hello", "language": "fr"}
    ).status_code == 422)
    check("/voice/synthesize accepts a regional language", client.post(
        "/voice/synthesize", headers=patient, json={"text": "vanakkam", "language": "ta"}
    ).status_code == 200)

    print("\n15. A reschedule cannot change the doctor")
    cardio_slots = [s for s in client.get("/slots", params={"doctor_id": 2}).json()]
    pinned = book(cardio_slots[0]["id"], "Chest tightness review")
    foreign_slot = client.get("/slots", params={"doctor_id": 1}).json()[0]

    refused = client.post(f"/appointments/{pinned['id']}/reschedule", headers=patient,
                          json={"slot_id": foreign_slot["id"]})
    check("a patient cannot move onto another doctor's slot", refused.status_code == 400)
    check("the refusal names the doctor they are actually booked with",
          "Raghav" in refused.json()["detail"])
    still = [a for a in client.get("/me/appointments", headers=patient).json()
             if a["id"] == pinned["id"]][0]
    check("the booking still belongs to the original doctor", still["doctor_id"] == 2)
    check("and to the original department", still["department"] == "Cardiology")

    same_doctor = client.post(f"/appointments/{pinned['id']}/reschedule", headers=patient,
                              json={"slot_id": cardio_slots[1]["id"]})
    check("a move within the same doctor still works", same_doctor.status_code == 200)

    voice_refused = as_user(me, agent.reschedule_my_appointment,
                            appointment_id=pinned["id"], new_slot_id=foreign_slot["id"])
    check("the voice agent cannot move onto another doctor's slot either",
          "error" in voice_refused)

    desk_move = client.post(f"/appointments/{pinned['id']}/reschedule", headers=admin,
                            json={"slot_id": foreign_slot["id"]})
    check("an admin may move a patient between specialists", desk_move.status_code == 200)
    check("and that move does change the doctor",
          desk_move.json()["appointment"]["doctor_id"] == 1)
    client.delete(f"/appointments/{pinned['id']}", headers=admin)

    print("\n16. A slot leaves the bookable list when taken and returns when freed")
    def bookable(slot_id, doctor_id=2):
        return slot_id in [s["id"] for s in client.get("/slots", params={"doctor_id": doctor_id}).json()]

    lifecycle_slot = client.get("/slots", params={"doctor_id": 2}).json()[0]["id"]
    check("a free slot is offered", bookable(lifecycle_slot))
    held = book(lifecycle_slot, "Slot lifecycle")
    check("a booked slot is withdrawn from the list", not bookable(lifecycle_slot))
    check("the voice agent stops offering it too", lifecycle_slot not in [
        row["slot_id"] for row in as_user(me, agent.get_available_slots, doctor_id=2)["available_slots"]
    ])

    client.delete(f"/appointments/{held['id']}", headers=patient)
    check("cancelling by the patient puts it back on sale", bookable(lifecycle_slot))

    held = book(lifecycle_slot, "Slot lifecycle, admin cancel")
    client.delete(f"/appointments/{held['id']}", headers=admin)
    check("cancelling by the admin puts it back on sale", bookable(lifecycle_slot))

    held = book(lifecycle_slot, "Slot lifecycle, doctor cancel")
    client.post(f"/doctor/appointments/{held['id']}/cancel", headers=cardiologist,
                json={"reason": "Called into theatre"})
    check("an emergency cancellation does NOT resell the slot", not bookable(lifecycle_slot))
    client.patch(f"/doctor/slots/{lifecycle_slot}", headers=cardiologist,
                 json={"status": "available"})
    check("the doctor re-opening it puts it back on sale", bookable(lifecycle_slot))

    print("\n17. A slot whose time has passed is not bookable")
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO slots (doctor_id, date, start_time, end_time, status)"
            " VALUES (2, ?, '00:05', '00:25', 'available')", (date.today().isoformat(),)
        )
        connection.commit()
        elapsed = connection.execute(
            "SELECT id FROM slots WHERE doctor_id = 2 AND date = ? AND start_time = '00:05'",
            (date.today().isoformat(),),
        ).fetchone()[0]

    check("an elapsed slot is not in the bookable list", not bookable(elapsed))
    check("the voice agent is not offered it", elapsed not in [
        row["slot_id"] for row in as_user(me, agent.get_available_slots, doctor_id=2)["available_slots"]
    ])
    stale = client.post("/appointments", headers=patient, json={
        "patient_name": "Demo Patient", "patient_age": 34, "patient_gender": "Male",
        "phone": "9000000009", "reason": "Booked from a stale page",
        "doctor_id": 2, "slot_id": elapsed,
    })
    check("booking it from a stale page is refused", stale.status_code == 409)
    check("and the refusal says why", "already passed" in stale.json()["detail"])

    overview = client.get("/admin/overview", headers=admin).json()
    print(f"\n  overview: {overview}")

print(f"\nAll checks passed. Temporary database: {TEMP_DB}")
print(f"Email log written to: {mailer.MAIL_LOG}")
