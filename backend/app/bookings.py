"""Appointment logic shared by the web form, the voice agent and the doctor desk.

Every booking, cancellation and reschedule goes through this module, so the
slot bookkeeping and the patient emails can only be written once.
"""
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.db import connect
from app.mailer import (
    send_appointment_reminder,
    send_booking_confirmation,
    send_doctor_cancellation,
    send_patient_cancellation,
    send_reschedule_confirmation,
)

# Statuses a doctor can close a finished consultation with.
OUTCOME_STATUSES = ("completed", "no_show")

# Full appointment view used by every response in the app.
APPOINTMENT_SELECT = """
SELECT
    appointments.id,
    appointments.patient_id,
    appointments.patient_name,
    appointments.patient_age,
    appointments.patient_gender,
    appointments.patient_email,
    appointments.phone,
    appointments.reason,
    appointments.status,
    appointments.cancel_reason,
    appointments.created_at,
    doctors.id AS doctor_id,
    doctors.name AS doctor_name,
    doctors.department,
    doctors.title AS doctor_title,
    doctors.location,
    slots.id AS slot_id,
    slots.date,
    slots.start_time,
    slots.end_time
FROM appointments
JOIN doctors ON doctors.id = appointments.doctor_id
JOIN slots ON slots.id = appointments.slot_id
"""


def format_appointment(row) -> dict:
    data = dict(row)
    data["reference_no"] = f"MV-2026-{data['id']:04d}"
    return data


def fetch_appointment(appointment_id: int) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            f"{APPOINTMENT_SELECT} WHERE appointments.id = ?", (appointment_id,)
        ).fetchone()
    return format_appointment(row) if row else None


def query_appointments(where: str = "", params: tuple = ()) -> list[dict]:
    clause = f" WHERE {where}" if where else ""
    with connect() as connection:
        rows = connection.execute(
            f"{APPOINTMENT_SELECT}{clause} ORDER BY slots.date DESC, slots.start_time DESC", params
        ).fetchall()
    return [format_appointment(row) for row in rows]


def create_appointment(
    *,
    patient_id: int | None,
    patient_name: str,
    patient_age: int,
    patient_gender: str,
    patient_email: str,
    phone: str,
    reason: str,
    doctor_id: int,
    slot_id: int,
) -> dict:
    """Books one consultation slot and emails the confirmation.

    Raises HTTPException on a bad doctor/slot or a slot someone else just took.
    """
    with connect() as connection:
        doctor = connection.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if doctor is None:
            raise HTTPException(status_code=404, detail="Selected medical specialist not found.")

        slot = connection.execute(
            "SELECT * FROM slots WHERE id = ? AND doctor_id = ?", (slot_id, doctor_id)
        ).fetchone()
        if slot is None:
            raise HTTPException(status_code=404, detail="Consultation slot not found for the selected doctor.")
        if slot["status"] != "available":
            raise HTTPException(status_code=409, detail="That consultation slot is no longer available.")
        # A page left open since this morning can still hold an elapsed slot id.
        if f"{slot['date']} {slot['start_time']}" <= datetime.now().strftime("%Y-%m-%d %H:%M"):
            raise HTTPException(
                status_code=409,
                detail="That consultation time has already passed. Please pick a later slot.",
            )

        cursor = connection.execute(
            """
            INSERT INTO appointments
                (patient_id, patient_name, patient_age, patient_gender, patient_email,
                 phone, reason, doctor_id, slot_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed')
            """,
            (
                patient_id,
                patient_name.strip(),
                patient_age,
                (patient_gender or "Not specified").strip(),
                (patient_email or "").strip().lower(),
                phone.strip(),
                reason.strip(),
                doctor_id,
                slot_id,
            ),
        )
        connection.execute("UPDATE slots SET status = 'booked' WHERE id = ?", (slot_id,))
        connection.commit()
        appointment_id = cursor.lastrowid

    appointment = fetch_appointment(appointment_id)
    appointment["email_sent"] = send_booking_confirmation(appointment)
    return appointment


def cancel_appointment_as_patient(appointment_id: int, patient_id: int | None = None) -> dict:
    """Patient (or admin, with patient_id=None) cancels. The slot goes back on sale."""
    appointment = _load_live_appointment(appointment_id, patient_id)

    with connect() as connection:
        connection.execute(
            "UPDATE appointments SET status = 'cancelled', cancel_reason = 'Cancelled by patient' WHERE id = ?",
            (appointment_id,),
        )
        connection.execute(
            "UPDATE slots SET status = 'available' WHERE id = ?", (appointment["slot_id"],)
        )
        connection.commit()

    send_patient_cancellation(appointment)
    return {
        "message": f"Appointment {appointment['reference_no']} cancelled and the slot released.",
        "appointment": fetch_appointment(appointment_id),
    }


def cancel_appointment_as_doctor(appointment_id: int, reason: str, doctor_id: int | None = None) -> dict:
    """Doctor is unavailable: the slot is blocked (not resold) and the patient is emailed."""
    appointment = _load_live_appointment(appointment_id)
    if doctor_id is not None and appointment["doctor_id"] != doctor_id:
        raise HTTPException(status_code=403, detail="This appointment belongs to another doctor.")

    reason = (reason or "Doctor unavailable due to an emergency").strip()
    with connect() as connection:
        connection.execute(
            "UPDATE appointments SET status = 'cancelled_by_doctor', cancel_reason = ? WHERE id = ?",
            (reason, appointment_id),
        )
        connection.execute(
            "UPDATE slots SET status = 'blocked' WHERE id = ?", (appointment["slot_id"],)
        )
        connection.commit()

    email_sent = send_doctor_cancellation(appointment, reason)
    return {
        "message": f"Appointment {appointment['reference_no']} cancelled. The patient has been notified by email.",
        "email_sent": email_sent,
        "appointment": fetch_appointment(appointment_id),
    }


def reschedule_appointment(
    appointment_id: int,
    new_slot_id: int,
    patient_id: int | None = None,
    allow_doctor_change: bool = False,
) -> dict:
    """Moves an appointment to another free slot *of the same doctor*.

    Works for live and doctor-cancelled bookings. A move onto a different doctor's slot
    is refused unless `allow_doctor_change` is set, which only an admin may do: the
    referral is to a named specialist, so silently turning a cardiology booking into a
    dermatology one would send the patient to the wrong clinic under the same token.
    """
    appointment = fetch_appointment(appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment record not found.")
    if patient_id is not None and appointment["patient_id"] != patient_id:
        raise HTTPException(status_code=403, detail="You can only reschedule your own appointments.")
    if appointment["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="This appointment was cancelled. Please book a new one.")

    old_slot_id = appointment["slot_id"]
    with connect() as connection:
        new_slot = connection.execute("SELECT * FROM slots WHERE id = ?", (new_slot_id,)).fetchone()
        if new_slot is None:
            raise HTTPException(status_code=404, detail="The selected slot does not exist.")
        if new_slot["status"] != "available":
            raise HTTPException(status_code=409, detail="That slot has just been taken. Please pick another one.")
        if not allow_doctor_change and new_slot["doctor_id"] != appointment["doctor_id"]:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"That slot belongs to another doctor. This appointment is with "
                    f"{appointment['doctor_name']} ({appointment['department']}); please pick one of "
                    f"their free slots, or book a new appointment with the other specialist."
                ),
            )

        connection.execute(
            """
            UPDATE appointments
            SET slot_id = ?, doctor_id = ?, status = 'confirmed', cancel_reason = ''
            WHERE id = ?
            """,
            (new_slot_id, new_slot["doctor_id"], appointment_id),
        )
        connection.execute("UPDATE slots SET status = 'booked' WHERE id = ?", (new_slot_id,))
        # A slot freed by the patient's own move can be resold; one blocked by the
        # doctor's emergency stays blocked.
        if appointment["status"] == "confirmed":
            connection.execute("UPDATE slots SET status = 'available' WHERE id = ?", (old_slot_id,))
        connection.commit()

    updated = fetch_appointment(appointment_id)
    updated["email_sent"] = send_reschedule_confirmation(updated)
    return {"message": f"Appointment {updated['reference_no']} rescheduled.", "appointment": updated}


def complete_appointment(appointment_id: int, status: str, doctor_id: int | None = None) -> dict:
    """Closes a finished consultation: 'completed' when the patient was seen, 'no_show' when not.

    The slot stays `booked` either way: the consultation time is spent, not released.
    """
    if status not in OUTCOME_STATUSES:
        raise HTTPException(status_code=400, detail="Outcome must be 'completed' or 'no_show'.")

    appointment = _load_live_appointment(appointment_id)
    if doctor_id is not None and appointment["doctor_id"] != doctor_id:
        raise HTTPException(status_code=403, detail="This appointment belongs to another doctor.")

    with connect() as connection:
        connection.execute("UPDATE appointments SET status = ? WHERE id = ?", (status, appointment_id))
        connection.commit()

    label = "marked as attended" if status == "completed" else "marked as a no-show"
    return {
        "message": f"Appointment {appointment['reference_no']} {label}.",
        "appointment": fetch_appointment(appointment_id),
    }


def send_due_reminders(days_ahead: int = 1) -> dict:
    """Emails a reminder for every confirmed appointment `days_ahead` days from today.

    `appointments.reminder_sent` keeps it to one reminder per booking, so re-running
    this is harmless.
    """
    target = (date.today() + timedelta(days=days_ahead)).isoformat()
    due = query_appointments(
        "appointments.status = 'confirmed' AND appointments.reminder_sent = 0 AND slots.date = ?",
        (target,),
    )

    sent = 0
    for appointment in due:
        send_appointment_reminder(appointment)
        with connect() as connection:
            connection.execute(
                "UPDATE appointments SET reminder_sent = 1 WHERE id = ?", (appointment["id"],)
            )
            connection.commit()
        sent += 1

    return {"date": target, "reminders_sent": sent}


def _load_live_appointment(appointment_id: int, patient_id: int | None = None) -> dict:
    appointment = fetch_appointment(appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment record not found.")
    if patient_id is not None and appointment["patient_id"] != patient_id:
        raise HTTPException(status_code=403, detail="You can only manage your own appointments.")
    if appointment["status"] != "confirmed":
        raise HTTPException(status_code=400, detail="This appointment is not active any more.")
    return appointment
