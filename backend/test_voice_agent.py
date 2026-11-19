"""
Interactive CLI Tester for MediVoc Voice AI Agent (LangGraph + Gemini 2.5 Flash + Local Speech)
Run this script to test the voice agent directly from the command line:
    python test_voice_agent.py
"""
import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv

# Load backend/.env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from app.agent import process_user_voice_message
from app.db import connect, initialize_database
from app.voice import text_to_speech_base64


def demo_patient() -> dict:
    """The voice agent books for a signed-in patient, so the CLI uses the demo account."""
    initialize_database()
    with connect() as connection:
        row = connection.execute(
            "SELECT id, name, email, phone, role, doctor_id FROM users WHERE role = 'patient' ORDER BY id LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}


async def main():
    print("=" * 65)
    print("  MediVoc Hospital Voice AI Agent - Interactive Test Console")
    print("=" * 65)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("\n[!] WARNING: GEMINI_API_KEY is not set in backend/.env")
        print("    Please set your key in backend/.env:")
        print("    GEMINI_API_KEY=your_gemini_api_key_here\n")
    else:
        print("[✓] Gemini API Key detected.")
        print(f"[✓] Using Model: {os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')}")

    patient = demo_patient()
    print(f"[✓] Signed in as patient: {patient.get('name')} <{patient.get('email')}>")

    session_id = f"test-cli-{os.getpid()}"
    print(f"[✓] Active Session ID: {session_id}")
    print("\nStarting conversation with MediVoc Voice Receptionist...")
    print("Type 'exit' or 'quit' to stop.\n")

    # Initial greeting test
    initial_prompt = "Hello, I want to book an appointment with a doctor."
    print(f"You: {initial_prompt}")
    res = await process_user_voice_message(session_id, initial_prompt, patient=patient)
    print(f"\nMediVoc Agent: {res['reply']}\n")

    while True:
        try:
            user_input = input("You > ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting voice test console. Goodbye!")
                break

            res = await process_user_voice_message(session_id, user_input, patient=patient)
            print(f"\nMediVoc Agent: {res['reply']}\n")

            if res.get("appointment"):
                appt = res["appointment"]
                print("-" * 50)
                print(f"🎉 APPOINTMENT CONFIRMED!")
                print(f"   Token: {appt.get('reference_no')}")
                print(f"   Patient: {appt.get('patient_name')} ({appt.get('patient_age')} yrs)")
                print(f"   Doctor: Dr. {appt.get('doctor_name')} ({appt.get('department')})")
                print(f"   Slot: {appt.get('date')} at {appt.get('start_time')}")
                print(f"   Location: {appt.get('location')}")
                print("-" * 50)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break


if __name__ == "__main__":
    asyncio.run(main())
