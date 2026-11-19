"""Database connection, schema and demo seed data for MediVoc."""
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "medivoc.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    experience_years INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    -- available | booked | blocked
    status TEXT NOT NULL DEFAULT 'available',
    FOREIGN KEY (doctor_id) REFERENCES doctors (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_slot_unique
    ON slots (doctor_id, date, start_time);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    -- patient | doctor | admin
    role TEXT NOT NULL,
    doctor_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (doctor_id) REFERENCES doctors (id)
);

CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER,
    patient_name TEXT NOT NULL,
    patient_age INTEGER NOT NULL,
    patient_gender TEXT NOT NULL DEFAULT 'Not specified',
    patient_email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL,
    reason TEXT NOT NULL,
    doctor_id INTEGER NOT NULL,
    slot_id INTEGER NOT NULL,
    -- confirmed | cancelled | cancelled_by_doctor | completed | no_show
    status TEXT NOT NULL DEFAULT 'confirmed',
    cancel_reason TEXT NOT NULL DEFAULT '',
    -- 1 once the day-before reminder email has gone out, so it is only sent once
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES users (id),
    FOREIGN KEY (doctor_id) REFERENCES doctors (id),
    FOREIGN KEY (slot_id) REFERENCES slots (id)
);

-- One live appointment per slot; cancelled rows are kept for history.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_live_appointment_per_slot
    ON appointments (slot_id) WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL,
    -- 0 = Monday ... 6 = Sunday
    weekday INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL DEFAULT 20,
    FOREIGN KEY (doctor_id) REFERENCES doctors (id)
);

CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (doctor_id) REFERENCES doctors (id)
);

CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
"""

DOCTOR_SEED = [
    (1, "Dr. Asha Menon", "General Medicine", "Senior Consultant Physician, MBBS, MD", "OPD Block A, Room 102", 14),
    (2, "Dr. Raghav Sharma", "Cardiology", "Chief Interventional Cardiologist, DM", "Heart Centre, OPD Block C, Room 204", 18),
    (3, "Dr. Nila Iyer", "Dermatology", "Consultant Dermatologist, MD (DVL)", "Skin & Laser Wing, OPD Block B, Room 115", 10),
    (4, "Dr. Karan Mehta", "Orthopedics", "Senior Orthopedic & Joint Surgeon, MS", "Ortho & Trauma Pavilion, Block D, Room 108", 15),
    (5, "Dr. Farah Khan", "Pediatrics", "Consultant Pediatrician, MD, DCH", "Mother & Child Centre, Block A, Room 105", 12),
]

# Mon-Fri morning clinic for every seeded doctor, 20 minute consultations.
SHIFT_SEED = [(doctor[0], weekday, "09:00", "12:00", 20) for doctor in DOCTOR_SEED for weekday in range(0, 5)]

# Demo accounts (plain passwords are hashed on first run).
USER_SEED = [
    ("Hospital Administrator", "admin@medivoc.com", "9000000000", "admin123", "admin", None),
    ("Dr. Asha Menon", "asha@medivoc.com", "9000000001", "doctor123", "doctor", 1),
    ("Dr. Raghav Sharma", "raghav@medivoc.com", "9000000002", "doctor123", "doctor", 2),
    ("Dr. Nila Iyer", "nila@medivoc.com", "9000000003", "doctor123", "doctor", 3),
    ("Dr. Karan Mehta", "karan@medivoc.com", "9000000004", "doctor123", "doctor", 4),
    ("Dr. Farah Khan", "farah@medivoc.com", "9000000005", "doctor123", "doctor", 5),
    ("Demo Patient", "patient@medivoc.com", "9000000009", "patient123", "patient", None),
]


def not_yet_started(prefix: str = "") -> tuple[str, list[str]]:
    """SQL fragment and params keeping only slots whose start time is still ahead.

    A plain `date >= today` leaves this morning's 09:00 consultation on sale all
    afternoon, so the comparison has to include the clock, not just the day.
    `prefix` is the table qualifier the caller's query uses, e.g. "slots.".
    """
    now = datetime.now()
    day = now.date().isoformat()
    return (
        f" AND ({prefix}date > ? OR ({prefix}date = ? AND {prefix}start_time > ?))",
        [day, day, now.strftime("%H:%M")],
    )


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def _migrate_appointments(connection: sqlite3.Connection) -> None:
    """Older builds had `slot_id ... UNIQUE` and no patient columns.

    A plain ALTER TABLE cannot drop that constraint, so the table is rebuilt once.
    """
    existing = _columns(connection, "appointments")
    if not existing or "patient_email" in existing:
        return

    connection.executescript(
        """
        ALTER TABLE appointments RENAME TO appointments_old;
        """
    )
    connection.executescript(SCHEMA)
    old_columns = _columns(connection, "appointments_old")
    shared = [c for c in old_columns if c in _columns(connection, "appointments")]
    column_list = ", ".join(shared)
    connection.execute(
        f"INSERT INTO appointments ({column_list}) SELECT {column_list} FROM appointments_old"
    )
    connection.execute("DROP TABLE appointments_old")
    print("[DB] Migrated appointments table to the RBAC schema.")


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not add
# them to an existing database, so they are applied one by one instead.
LATER_COLUMNS = [
    ("appointments", "reminder_sent", "INTEGER NOT NULL DEFAULT 0"),
]


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    for table, column, definition in LATER_COLUMNS:
        if column not in _columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"[DB] Added {table}.{column}.")


def initialize_database() -> None:
    from app.auth import hash_password  # imported here to avoid a circular import

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)
        _migrate_appointments(connection)
        _add_missing_columns(connection)

        if connection.execute("SELECT COUNT(*) FROM doctors").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO doctors (id, name, department, title, location, experience_years)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                DOCTOR_SEED,
            )

        if connection.execute("SELECT COUNT(*) FROM shifts").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO shifts (doctor_id, weekday, start_time, end_time, slot_minutes)"
                " VALUES (?, ?, ?, ?, ?)",
                SHIFT_SEED,
            )

        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            connection.executemany(
                "INSERT INTO users (name, email, phone, password_hash, role, doctor_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (name, email, phone, hash_password(password), role, doctor_id)
                    for name, email, phone, password, role, doctor_id in USER_SEED
                ],
            )
            print("[DB] Seeded demo accounts (see backend/README_ACCOUNTS.md).")

        connection.commit()

    # Fill the calendar so the portal is never empty on a fresh install.
    from app.schedule import generate_slots

    generate_slots(days=21)
