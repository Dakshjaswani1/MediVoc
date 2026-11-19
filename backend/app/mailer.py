"""Appointment emails.

Uses stdlib smtplib. When SMTP is not configured (the usual case on a demo
laptop) the message is printed and appended to backend/data/sent_emails.log
so the flow is still demonstrable.
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
MAIL_LOG = BASE_DIR / "data" / "sent_emails.log"

HOSPITAL = "MediVoc Memorial Hospital"


def _smtp_config() -> dict | None:
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "sender": os.getenv("SMTP_FROM", user),
    }


def send_email(to: str, subject: str, body: str) -> bool:
    """Sends one plain-text email. Returns True when SMTP actually delivered it."""
    if not to:
        print("[Mail] Skipped: no recipient address on this appointment.")
        return False

    config = _smtp_config()
    if config is None:
        _log_email(to, subject, body, delivered=False)
        print(f"[Mail] SMTP not configured - logged mail to {to} ('{subject}')")
        return False

    message = EmailMessage()
    message["From"] = f"{HOSPITAL} <{config['sender']}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as server:
            server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(message)
        _log_email(to, subject, body, delivered=True)
        print(f"[Mail] Sent to {to}: {subject}")
        return True
    except Exception as error:  # a failed email must never break a booking
        print(f"[Mail] Delivery failed ({error}) - message logged instead.")
        _log_email(to, subject, body, delivered=False)
        return False


def _log_email(to: str, subject: str, body: str, delivered: bool) -> None:
    MAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    status = "SENT" if delivered else "NOT SENT (logged only)"
    with MAIL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n{'=' * 70}\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {status}\n"
            f"To: {to}\nSubject: {subject}\n{'-' * 70}\n{body}\n"
        )


# ------------------------- message templates -------------------------

def _details_block(appointment: dict) -> str:
    return (
        f"  Token ID        : {appointment['reference_no']}\n"
        f"  Patient         : {appointment['patient_name']} "
        f"({appointment['patient_age']} yrs, {appointment.get('patient_gender', 'Not specified')})\n"
        f"  Doctor          : {appointment['doctor_name']}\n"
        f"  Designation     : {appointment.get('doctor_title', '')}\n"
        f"  Department      : {appointment['department']}\n"
        f"  Date            : {appointment['date']}\n"
        f"  Time            : {appointment['start_time']} - {appointment.get('end_time', '')}\n"
        f"  Location        : {appointment.get('location', 'OPD Reception')}\n"
        f"  Contact         : {appointment['phone']}\n"
        f"  Symptoms/Reason : {appointment['reason']}\n"
    )


def send_booking_confirmation(appointment: dict) -> bool:
    body = (
        f"Dear {appointment['patient_name']},\n\n"
        f"Your outpatient consultation at {HOSPITAL} is confirmed.\n\n"
        f"{_details_block(appointment)}\n"
        "Please arrive 15 minutes early and carry a photo ID along with any previous\n"
        "prescriptions or reports. Show the token ID above at the OPD reception desk.\n\n"
        "If you cannot attend, please cancel from your patient portal so the slot can\n"
        "be offered to another patient.\n\n"
        f"Regards,\nOutpatient Desk\n{HOSPITAL}\n"
    )
    return send_email(
        appointment.get("patient_email", ""),
        f"Appointment confirmed - {appointment['reference_no']} - {appointment['date']} {appointment['start_time']}",
        body,
    )


def send_doctor_cancellation(appointment: dict, reason: str) -> bool:
    body = (
        f"Dear {appointment['patient_name']},\n\n"
        f"We are sorry to inform you that your consultation with {appointment['doctor_name']} "
        f"on {appointment['date']} at {appointment['start_time']} has been CANCELLED because the\n"
        "doctor is unavailable.\n\n"
        f"  Reason: {reason or 'Emergency duty'}\n\n"
        f"{_details_block(appointment)}\n"
        "No charges apply. Please sign in to the MediVoc patient portal and use\n"
        "'My Appointments' -> 'Reschedule' to pick another available slot, or call the\n"
        "OPD desk on (080) 4000-1000 and our staff will rebook it for you.\n\n"
        "We apologise sincerely for the inconvenience.\n\n"
        f"Regards,\nOutpatient Desk\n{HOSPITAL}\n"
    )
    return send_email(
        appointment.get("patient_email", ""),
        f"IMPORTANT: Appointment cancelled - {appointment['reference_no']} - please reschedule",
        body,
    )


def send_reschedule_confirmation(appointment: dict) -> bool:
    body = (
        f"Dear {appointment['patient_name']},\n\n"
        "Your consultation has been rescheduled. The new details are below.\n\n"
        f"{_details_block(appointment)}\n"
        "Your token ID stays the same. Please arrive 15 minutes early.\n\n"
        f"Regards,\nOutpatient Desk\n{HOSPITAL}\n"
    )
    return send_email(
        appointment.get("patient_email", ""),
        f"Appointment rescheduled - {appointment['reference_no']} - {appointment['date']} {appointment['start_time']}",
        body,
    )


def send_appointment_reminder(appointment: dict) -> bool:
    body = (
        f"Dear {appointment['patient_name']},\n\n"
        f"This is a reminder of your outpatient consultation at {HOSPITAL}.\n\n"
        f"{_details_block(appointment)}\n"
        "Please arrive 15 minutes early and carry a photo ID along with any previous\n"
        "prescriptions or reports. Show the token ID above at the OPD reception desk.\n\n"
        "If you can no longer attend, please cancel from your patient portal or ask our\n"
        "Voice AI desk to cancel it, so the slot can be offered to another patient.\n\n"
        f"Regards,\nOutpatient Desk\n{HOSPITAL}\n"
    )
    return send_email(
        appointment.get("patient_email", ""),
        f"Reminder: appointment on {appointment['date']} at {appointment['start_time']}"
        f" - {appointment['reference_no']}",
        body,
    )


def send_patient_cancellation(appointment: dict) -> bool:
    body = (
        f"Dear {appointment['patient_name']},\n\n"
        f"Your appointment {appointment['reference_no']} with {appointment['doctor_name']} on "
        f"{appointment['date']} at {appointment['start_time']} has been cancelled as requested.\n\n"
        "The slot has been released for other patients. You are welcome to book again at\n"
        "any time from the MediVoc patient portal.\n\n"
        f"Regards,\nOutpatient Desk\n{HOSPITAL}\n"
    )
    return send_email(
        appointment.get("patient_email", ""),
        f"Appointment cancelled - {appointment['reference_no']}",
        body,
    )
