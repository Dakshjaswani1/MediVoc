"""Doctor desk: own slots, own appointments, emergency cancellation."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import require_role
from app.bookings import cancel_appointment_as_doctor, complete_appointment, query_appointments
from app.db import connect

router = APIRouter(prefix="/doctor", tags=["doctor"])
doctor_only = require_role("doctor")


def _doctor_id(user: dict) -> int:
    if not user.get("doctor_id"):
        raise HTTPException(status_code=403, detail="This login is not linked to a doctor profile.")
    return user["doctor_id"]


@router.get("/profile")
def profile(user: dict = Depends(doctor_only)) -> dict:
    doctor_id = _doctor_id(user)
    with connect() as connection:
        doctor = connection.execute("SELECT * FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        shifts = connection.execute(
            "SELECT * FROM shifts WHERE doctor_id = ? ORDER BY weekday, start_time", (doctor_id,)
        ).fetchall()
        leaves = connection.execute(
            "SELECT * FROM leaves WHERE doctor_id = ? ORDER BY date", (doctor_id,)
        ).fetchall()
        holidays = connection.execute(
            "SELECT * FROM holidays WHERE date >= ? ORDER BY date", (date.today().isoformat(),)
        ).fetchall()
        counts = connection.execute(
            """
            SELECT
                SUM(status = 'available') AS available,
                SUM(status = 'booked') AS booked,
                SUM(status = 'blocked') AS blocked
            FROM slots WHERE doctor_id = ? AND date >= ?
            """,
            (doctor_id, date.today().isoformat()),
        ).fetchone()

    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor profile not found.")

    return {
        "doctor": dict(doctor),
        "shifts": [dict(row) for row in shifts],
        "leaves": [dict(row) for row in leaves],
        "holidays": [dict(row) for row in holidays],
        "slot_counts": {key: (counts[key] or 0) for key in ("available", "booked", "blocked")},
    }


def list_doctor_slots(
    doctor_id: int, status: str | None = None, upcoming_only: bool = True, day: str | None = None
) -> list[dict]:
    """All slots for one doctor, with the patient attached to booked ones.

    Shared by the doctor desk REST route and the doctor's voice agent.
    """
    query = """
        SELECT
            slots.*,
            appointments.id AS appointment_id,
            appointments.patient_name,
            appointments.patient_age,
            appointments.patient_gender,
            appointments.phone,
            appointments.patient_email,
            appointments.reason,
            appointments.status AS appointment_status
        FROM slots
        LEFT JOIN appointments
            ON appointments.slot_id = slots.id
           AND appointments.status IN ('confirmed', 'completed', 'no_show')
        WHERE slots.doctor_id = ?
    """
    params: list = [doctor_id]
    if upcoming_only:
        query += " AND slots.date >= ?"
        params.append(date.today().isoformat())
    if status:
        query += " AND slots.status = ?"
        params.append(status)
    if day:
        query += " AND slots.date = ?"
        params.append(day)
    query += " ORDER BY slots.date, slots.start_time"

    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/slots")
def my_slots(
    status: str | None = None,
    upcoming_only: bool = True,
    user: dict = Depends(doctor_only),
) -> list[dict]:
    """All slots for the signed-in doctor, with the patient attached to booked ones."""
    return list_doctor_slots(_doctor_id(user), status=status, upcoming_only=upcoming_only)


@router.get("/appointments")
def my_appointments(user: dict = Depends(doctor_only)) -> list[dict]:
    return query_appointments("appointments.doctor_id = ?", (_doctor_id(user),))


class SlotStatusUpdate(BaseModel):
    status: str = Field(pattern="^(available|blocked)$")


@router.patch("/slots/{slot_id}")
def update_slot(slot_id: int, payload: SlotStatusUpdate, user: dict = Depends(doctor_only)) -> dict:
    """Blocks a free slot (so nobody can book it) or re-opens a blocked one."""
    doctor_id = _doctor_id(user)
    with connect() as connection:
        slot = connection.execute(
            "SELECT * FROM slots WHERE id = ? AND doctor_id = ?", (slot_id, doctor_id)
        ).fetchone()
        if slot is None:
            raise HTTPException(status_code=404, detail="Slot not found in your calendar.")
        if slot["status"] == "booked":
            raise HTTPException(
                status_code=409,
                detail="This slot is booked. Use 'Cancel consultation' so the patient is informed by email.",
            )
        connection.execute("UPDATE slots SET status = ? WHERE id = ?", (payload.status, slot_id))
        connection.commit()

    verb = "blocked" if payload.status == "blocked" else "re-opened"
    return {"message": f"Slot on {slot['date']} at {slot['start_time']} {verb}.", "slot_id": slot_id}


class SlotCreate(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")


@router.post("/slots", status_code=201)
def add_slot(payload: SlotCreate, user: dict = Depends(doctor_only)) -> dict:
    """Adds one extra consultation slot outside the regular shift."""
    doctor_id = _doctor_id(user)
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=400, detail="End time must be after the start time.")

    with connect() as connection:
        clash = connection.execute(
            "SELECT 1 FROM slots WHERE doctor_id = ? AND date = ? AND start_time = ?",
            (doctor_id, payload.date, payload.start_time),
        ).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail="You already have a slot starting at that time.")
        cursor = connection.execute(
            "INSERT INTO slots (doctor_id, date, start_time, end_time, status) VALUES (?, ?, ?, ?, 'available')",
            (doctor_id, payload.date, payload.start_time, payload.end_time),
        )
        connection.commit()

    return {"slot_id": cursor.lastrowid, "message": "Extra slot opened for booking."}


class OutcomeRequest(BaseModel):
    status: str = Field(pattern="^(completed|no_show)$")


@router.post("/appointments/{appointment_id}/outcome")
def record_outcome(
    appointment_id: int, payload: OutcomeRequest, user: dict = Depends(doctor_only)
) -> dict:
    """Closes a consultation after the clinic: the patient was seen, or did not turn up."""
    return complete_appointment(appointment_id, payload.status, doctor_id=_doctor_id(user))


class CancelRequest(BaseModel):
    reason: str = Field(default="", max_length=200)


@router.post("/appointments/{appointment_id}/cancel")
def cancel_consultation(
    appointment_id: int, payload: CancelRequest, user: dict = Depends(doctor_only)
) -> dict:
    """Emergency cancellation: blocks the slot and emails the patient to reschedule."""
    return cancel_appointment_as_doctor(appointment_id, payload.reason, doctor_id=_doctor_id(user))
