"""Doctor shifts, leaves, hospital holidays and slot generation (admin area)."""
import re
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.auth import hash_password, require_role
from app.db import connect

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require_role("admin")

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --------------------------- slot generation -------------------------

def _parse_time(value: str) -> time:
    return datetime.strptime(value.strip(), "%H:%M").time()


def _slot_windows(start: str, end: str, minutes: int) -> list[tuple[str, str]]:
    """Splits a shift into back-to-back consultation windows."""
    cursor = datetime.combine(date.today(), _parse_time(start))
    finish = datetime.combine(date.today(), _parse_time(end))
    step = timedelta(minutes=max(5, minutes))
    windows = []
    while cursor + step <= finish:
        windows.append((cursor.strftime("%H:%M"), (cursor + step).strftime("%H:%M")))
        cursor += step
    return windows


def generate_slots(days: int = 21, start_from: date | None = None) -> dict:
    """Creates consultation slots from each doctor's shifts for the next `days` days.

    Skips hospital holidays and doctor leave days. Existing slots are left alone
    (the unique index on doctor+date+start_time makes this safe to re-run).
    """
    first_day = start_from or date.today()
    created = 0
    skipped_days = 0

    with connect() as connection:
        shifts = connection.execute("SELECT * FROM shifts ORDER BY doctor_id, weekday").fetchall()
        holidays = {row["date"] for row in connection.execute("SELECT date FROM holidays").fetchall()}
        leaves = {
            (row["doctor_id"], row["date"])
            for row in connection.execute("SELECT doctor_id, date FROM leaves").fetchall()
        }

        for offset in range(days):
            day = first_day + timedelta(days=offset)
            day_text = day.isoformat()
            if day_text in holidays:
                skipped_days += 1
                continue

            for shift in shifts:
                if shift["weekday"] != day.weekday():
                    continue
                if (shift["doctor_id"], day_text) in leaves:
                    continue
                for start_time, end_time in _slot_windows(
                    shift["start_time"], shift["end_time"], shift["slot_minutes"]
                ):
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO slots (doctor_id, date, start_time, end_time, status)"
                        " VALUES (?, ?, ?, ?, 'available')",
                        (shift["doctor_id"], day_text, start_time, end_time),
                    )
                    created += cursor.rowcount

        connection.commit()

    return {
        "created_slots": created,
        "days": days,
        "from": first_day.isoformat(),
        "to": (first_day + timedelta(days=days - 1)).isoformat(),
        "skipped_holidays": skipped_days,
    }


def _release_slots(connection, doctor_id: int | None, day: str) -> list[dict]:
    """Removes free slots on a day off and returns the appointments that now need rebooking."""
    params: list = [day]
    doctor_filter = ""
    if doctor_id is not None:
        doctor_filter = " AND doctor_id = ?"
        params.append(doctor_id)

    affected = connection.execute(
        """
        SELECT appointments.id
        FROM appointments
        JOIN slots ON slots.id = appointments.slot_id
        WHERE appointments.status = 'confirmed' AND slots.date = ?"""
        + (" AND slots.doctor_id = ?" if doctor_id is not None else ""),
        params,
    ).fetchall()

    connection.execute(
        f"DELETE FROM slots WHERE date = ? AND status = 'available'{doctor_filter}",
        params,
    )
    return [dict(row) for row in affected]


# ------------------------------- shifts ------------------------------

class ShiftCreate(BaseModel):
    doctor_id: int
    weekday: int = Field(ge=0, le=6, description="0 = Monday ... 6 = Sunday")
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    slot_minutes: int = Field(default=20, ge=5, le=120)


@router.get("/shifts")
def list_shifts(doctor_id: int | None = None, _: dict = Depends(admin_only)) -> list[dict]:
    with connect() as connection:
        query = (
            "SELECT shifts.*, doctors.name AS doctor_name, doctors.department"
            " FROM shifts JOIN doctors ON doctors.id = shifts.doctor_id"
        )
        params: list = []
        if doctor_id is not None:
            query += " WHERE shifts.doctor_id = ?"
            params.append(doctor_id)
        query += " ORDER BY doctors.name, shifts.weekday, shifts.start_time"
        rows = connection.execute(query, params).fetchall()
    return [dict(row, weekday_name=WEEKDAY_NAMES[row["weekday"]]) for row in rows]


@router.post("/shifts", status_code=201)
def create_shift(payload: ShiftCreate, _: dict = Depends(admin_only)) -> dict:
    if _parse_time(payload.end_time) <= _parse_time(payload.start_time):
        raise HTTPException(status_code=400, detail="Shift end time must be after the start time.")

    with connect() as connection:
        doctor = connection.execute("SELECT 1 FROM doctors WHERE id = ?", (payload.doctor_id,)).fetchone()
        if doctor is None:
            raise HTTPException(status_code=404, detail="Doctor not found.")

        clash = connection.execute(
            """
            SELECT 1 FROM shifts
            WHERE doctor_id = ? AND weekday = ? AND start_time < ? AND end_time > ?
            """,
            (payload.doctor_id, payload.weekday, payload.end_time, payload.start_time),
        ).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail="This doctor already has an overlapping shift that day.")

        cursor = connection.execute(
            "INSERT INTO shifts (doctor_id, weekday, start_time, end_time, slot_minutes) VALUES (?, ?, ?, ?, ?)",
            (payload.doctor_id, payload.weekday, payload.start_time, payload.end_time, payload.slot_minutes),
        )
        connection.commit()
        shift_id = cursor.lastrowid

    result = generate_slots()
    return {"id": shift_id, "message": "Shift added.", "slots": result}


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int, _: dict = Depends(admin_only)) -> dict:
    with connect() as connection:
        shift = connection.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        if shift is None:
            raise HTTPException(status_code=404, detail="Shift not found.")

        # Drop only future, still-free slots that came from this shift.
        removed = connection.execute(
            """
            DELETE FROM slots
            WHERE doctor_id = ? AND status = 'available' AND date >= ?
              AND start_time >= ? AND start_time < ?
              AND CAST(strftime('%w', date) AS INTEGER) = ?
            """,
            (
                shift["doctor_id"],
                date.today().isoformat(),
                shift["start_time"],
                shift["end_time"],
                (shift["weekday"] + 1) % 7,  # SQLite %w: 0 = Sunday
            ),
        ).rowcount
        connection.execute("DELETE FROM shifts WHERE id = ?", (shift_id,))
        connection.commit()

    return {"message": "Shift removed.", "removed_free_slots": removed}


# ------------------------------- leaves ------------------------------

class LeaveCreate(BaseModel):
    doctor_id: int
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: str = Field(default="", max_length=200)


@router.get("/leaves")
def list_leaves(_: dict = Depends(admin_only)) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT leaves.*, doctors.name AS doctor_name FROM leaves"
            " JOIN doctors ON doctors.id = leaves.doctor_id ORDER BY leaves.date DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def record_leave(doctor_id: int, day: str, reason: str = "") -> dict:
    """Books one doctor off for a day: free slots go, booked ones are cancelled and emailed.

    Shared by the admin REST route and the doctor's voice agent, so a leave declared
    either way clears the calendar the same way.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise HTTPException(status_code=400, detail="Leave date must be in YYYY-MM-DD form.")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{day} is not a real calendar date.")

    reason = (reason or "").strip()
    with connect() as connection:
        doctor = connection.execute("SELECT name FROM doctors WHERE id = ?", (doctor_id,)).fetchone()
        if doctor is None:
            raise HTTPException(status_code=404, detail="Doctor not found.")
        if connection.execute(
            "SELECT 1 FROM leaves WHERE doctor_id = ? AND date = ?", (doctor_id, day)
        ).fetchone():
            raise HTTPException(status_code=409, detail="Leave is already recorded for this doctor on that date.")

        connection.execute(
            "INSERT INTO leaves (doctor_id, date, reason) VALUES (?, ?, ?)",
            (doctor_id, day, reason),
        )
        pending = _release_slots(connection, doctor_id, day)
        connection.commit()

    cancelled = _cancel_for_day(pending, reason or "Doctor on leave")
    return {
        "message": "Leave recorded. Free slots on that day were removed.",
        "doctor_name": doctor["name"],
        "date": day,
        "reason": reason,
        "cancelled_appointments": cancelled,
    }


@router.post("/leaves", status_code=201)
def create_leave(payload: LeaveCreate, _: dict = Depends(admin_only)) -> dict:
    return record_leave(payload.doctor_id, payload.date, payload.reason)


@router.delete("/leaves/{leave_id}")
def delete_leave(leave_id: int, _: dict = Depends(admin_only)) -> dict:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM leaves WHERE id = ?", (leave_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Leave record not found.")
        connection.execute("DELETE FROM leaves WHERE id = ?", (leave_id,))
        connection.commit()
    return {"message": "Leave removed.", "slots": generate_slots()}


# ------------------------------ holidays -----------------------------

class HolidayCreate(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    name: str = Field(min_length=2, max_length=100)


@router.get("/holidays")
def list_holidays(_: dict = Depends(admin_only)) -> list[dict]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM holidays ORDER BY date").fetchall()
    return [dict(row) for row in rows]


@router.post("/holidays", status_code=201)
def create_holiday(payload: HolidayCreate, _: dict = Depends(admin_only)) -> dict:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM holidays WHERE date = ?", (payload.date,)).fetchone():
            raise HTTPException(status_code=409, detail="This date is already marked as a holiday.")
        connection.execute(
            "INSERT INTO holidays (date, name) VALUES (?, ?)", (payload.date, payload.name.strip())
        )
        pending = _release_slots(connection, None, payload.date)
        connection.commit()

    cancelled = _cancel_for_day(pending, f"Hospital holiday: {payload.name.strip()}")
    return {
        "message": "Holiday added. The OPD calendar for that date has been cleared.",
        "cancelled_appointments": cancelled,
    }


@router.delete("/holidays/{holiday_id}")
def delete_holiday(holiday_id: int, _: dict = Depends(admin_only)) -> dict:
    with connect() as connection:
        if connection.execute("SELECT 1 FROM holidays WHERE id = ?", (holiday_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Holiday not found.")
        connection.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
        connection.commit()
    return {"message": "Holiday removed.", "slots": generate_slots()}


def _cancel_for_day(pending: list[dict], reason: str) -> int:
    """Cancels (and emails) the appointments made unavailable by a leave or holiday."""
    from app.bookings import cancel_appointment_as_doctor

    for appointment in pending:
        cancel_appointment_as_doctor(appointment["id"], reason)
    return len(pending)


# ------------------------- slots & staff admin -----------------------

class GenerateRequest(BaseModel):
    days: int = Field(default=21, ge=1, le=120)


@router.post("/generate-slots")
def generate(payload: GenerateRequest, _: dict = Depends(admin_only)) -> dict:
    return generate_slots(days=payload.days)


class ReminderRequest(BaseModel):
    days_ahead: int = Field(default=1, ge=0, le=14)


@router.post("/send-reminders")
def send_reminders(payload: ReminderRequest, _: dict = Depends(admin_only)) -> dict:
    """Sends the day-before reminder emails by hand. The server also does this hourly."""
    from app.bookings import send_due_reminders

    result = send_due_reminders(payload.days_ahead)
    return {
        "message": f"{result['reminders_sent']} reminder email(s) sent for {result['date']}.",
        **result,
    }


class DoctorCreate(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    department: str = Field(min_length=3, max_length=60)
    title: str = Field(min_length=3, max_length=120)
    location: str = Field(min_length=3, max_length=120)
    experience_years: int = Field(ge=0, le=60)
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)


@router.post("/doctors", status_code=201)
def create_doctor(payload: DoctorCreate, _: dict = Depends(admin_only)) -> dict:
    """Adds a doctor plus the login account that doctor will use."""
    with connect() as connection:
        if connection.execute("SELECT 1 FROM users WHERE email = ?", (payload.email.lower(),)).fetchone():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        cursor = connection.execute(
            "INSERT INTO doctors (name, department, title, location, experience_years) VALUES (?, ?, ?, ?, ?)",
            (
                payload.name.strip(),
                payload.department.strip(),
                payload.title.strip(),
                payload.location.strip(),
                payload.experience_years,
            ),
        )
        doctor_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO users (name, email, phone, password_hash, role, doctor_id)"
            " VALUES (?, ?, '', ?, 'doctor', ?)",
            (payload.name.strip(), payload.email.lower(), hash_password(payload.password), doctor_id),
        )
        connection.commit()

    return {"doctor_id": doctor_id, "message": f"{payload.name} added. Assign shifts to open consultation slots."}


@router.get("/overview")
def overview(_: dict = Depends(admin_only)) -> dict:
    with connect() as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM doctors) AS doctors,
                (SELECT COUNT(*) FROM users WHERE role = 'patient') AS patients,
                (SELECT COUNT(*) FROM slots WHERE status = 'available') AS available_slots,
                (SELECT COUNT(*) FROM slots WHERE status = 'booked') AS booked_slots,
                (SELECT COUNT(*) FROM slots WHERE status = 'blocked') AS blocked_slots,
                (SELECT COUNT(*) FROM appointments WHERE status = 'confirmed') AS confirmed_appointments,
                (SELECT COUNT(*) FROM appointments WHERE status = 'cancelled_by_doctor') AS doctor_cancellations,
                (SELECT COUNT(*) FROM appointments WHERE status = 'completed') AS completed_consultations,
                (SELECT COUNT(*) FROM appointments WHERE status = 'no_show') AS no_shows,
                (SELECT COUNT(*) FROM shifts) AS shifts,
                (SELECT COUNT(*) FROM leaves) AS leaves,
                (SELECT COUNT(*) FROM holidays) AS holidays
            """
        ).fetchone()
    return dict(counts)
