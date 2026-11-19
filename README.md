# MediVoc

MediVoc is a final-year capstone project: a hospital outpatient appointment system with
role-based access, an admin scheduling desk, a doctor desk, and both form-based and
voice-AI patient booking.

## Features

### Roles (RBAC)
Signed tokens (HMAC, stdlib) + pbkdf2 password hashes. Every endpoint is guarded by role.

- **Patient** — register/sign in, book by form or by voice, see own appointments, cancel, reschedule
- **Doctor** — own calendar (available / booked / blocked), block & re-open slots, open extra slots, emergency cancellation, close a consultation as attended or no-show, review the clinic / cancel / mark leave by voice
- **Admin** — doctors, weekly shifts, doctor leaves, hospital holidays, slot generation, full OPD ledger

Demo logins: see `backend/README_ACCOUNTS.md`.

### Admin scheduling desk
- **Shifts** per doctor per weekday (start, end, minutes per patient). Slots are generated from shifts.
- **Leaves** per doctor per date — clears that doctor's free slots and cancels+emails anyone already booked.
- **Holidays** per date — clears the whole OPD calendar for that day, same cancellation emails.
- **Generate slots** for the next N days, skipping holidays and leave days. Safe to re-run.
- A slot leaves the bookable list the moment it is booked and returns when the booking is
  cancelled by the patient or the admin. An **emergency** cancellation by the doctor is the one
  exception: that slot is blocked, because the doctor is not there to see anyone in it. The doctor
  puts it back on sale with **Re-open**. Slots whose start time has passed drop off the list too.

### Doctor desk
- Calendar grouped by day, filtered by slot status, booked slots show patient name, age, phone and symptoms.
- Block a free slot or re-open it. Add an extra slot outside the regular shift.
- **Emergency cancellation**: the patient is emailed to reschedule and the slot is *blocked*, not resold.
- **Consultation outcome**: once the clinic has run, close each booking as **attended** (`completed`) or
  **no-show** (`no_show`). The slot stays booked either way, because the consultation time is spent.
  No-show and completion counts appear on the admin overview.

### Doctor voice desk
The same voice widget, with a staff toolset chosen from the signed-in role. A doctor can:
- **Hear their clinic** — "what does tomorrow look like?" reads the booked and free counts; the
  agent will give one patient's details when asked, and never another doctor's.
- **Cancel a consultation** — it reads the patient, date and time back and waits for a clear yes,
  then runs the same emergency cancellation as the desk button: the patient is emailed and the
  slot is blocked, not resold.
- **Mark a day of leave** — it works the date out from today, warns how many patients will be
  cancelled, waits for a yes, then clears the day. The administrator sees that leave as if they
  had declared it themselves.

Patients cannot reach these tools: each one re-checks the role, so the toolset is not the only guard.
An admin has no voice desk and is refused at `/voice/chat`.

### Patient booking
- **Form booking** — department, specialist, slot, demographics, printable token slip.
- **Voice AI booking** — LangChain + LangGraph ReAct agent on **Gemini 2.5 Flash**, local **Whisper** STT and **Edge-TTS** speech. The agent knows who is signed in, so it never asks for name or email.
- **Book, cancel or reschedule by voice** — the agent has tools for all three and is told to read the
  doctor, date and time back and wait for a clear yes before it cancels anything. It can only touch the
  signed-in patient's own appointments.
- **Six languages** — English, Hindi, Tamil, Malayalam, Telugu and Kannada, picked from a dropdown in the
  voice widget. One code drives all three stages: the browser's speech recognition, Whisper STT and the
  Edge-TTS neural voice. Doctor names and the token id stay in their original form so they can be
  repeated at the reception desk.
- **Typed chat** in the same voice widget as a fallback.
- **My Appointments** — status, hospital cancellation notes, cancel, reschedule (token number is kept).
  A reschedule stays with the same doctor, by form or by voice: the referral is to a named specialist,
  so only an admin can move a patient between specialists.

### Email
Confirmation, **day-before reminder**, doctor cancellation, reschedule and patient cancellation emails
via stdlib `smtplib`. Reminders go out automatically once an hour (`reminder_sent` on the appointment
keeps it to one per booking) and the admin overview has a button to send tomorrow's batch immediately.
Without SMTP credentials the system runs in demo mode: every message is printed and appended to
`backend/data/sent_emails.log`.

## Environment

`backend/.env` (copy from `backend/.env.example`):

```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
MEDIVOC_SECRET=any-random-string
# Leave SMTP_HOST empty for demo mode (emails written to data/sent_emails.log)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your_16_char_app_password
```

## Run

```powershell
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload     # http://127.0.0.1:8000

cd ../frontend
npm install
npm run dev                       # http://localhost:5173
```

The database, demo accounts and three weeks of slots are created on first start.

## Tests

```powershell
cd backend
python test_flow.py          # end-to-end: roles, shifts, leaves, holidays, booking, emails, cancel, reschedule
python test_voice_agent.py   # interactive voice agent console (needs GEMINI_API_KEY)
```

`test_flow.py` runs against a throwaway database and asserts every step, so a broken flow fails loudly.

## Backend layout

| File | Responsibility |
|------|----------------|
| `app/db.py` | schema, migration, demo seed data |
| `app/auth.py` | password hashing, signed tokens, role guards, `/auth/*` |
| `app/bookings.py` | book / cancel / doctor-cancel / reschedule / close-outcome / reminders — shared by form, voice and doctor desk |
| `app/mailer.py` | SMTP sending and the four email templates |
| `app/schedule.py` | `/admin/*` — shifts, leaves, holidays, slot generation, add doctor |
| `app/doctor.py` | `/doctor/*` — own slots, slot updates, emergency cancellation |
| `app/agent.py` | the two LangGraph voice agents (patient receptionist, doctor desk) and their tools |
| `app/voice.py` | Whisper STT, Edge-TTS / pyttsx3 speech, the per-language voice map |
| `app/main.py` | app setup, directory, patient appointment and voice routes |

## Demo flow

1. Sign in as **admin**, add a shift or declare a holiday, press **Generate Slots**.
2. Sign in as **patient**, book by form or click **Talk to Voice AI** and speak your symptoms. Check the email log.
3. Sign in as the **doctor**, open the booked slot, hit **Cancel (emergency)** with a reason.
4. Back as the **patient**, see the hospital note in My Appointments and **Reschedule** into a free slot.
5. Open the voice desk, pick a language, and say "cancel my appointment" or "move my appointment to
   Thursday morning" — the agent confirms the details with you before it changes anything.
6. As the **doctor**, close a past booking with **Mark attended** or **No-show**, then open
   **Talk to Desk Assistant** and say "what does tomorrow look like?", "cancel my eleven o'clock,
   I'm in theatre" or "put me on leave next Thursday" — the assistant confirms before it changes
   anything, and the affected patients are emailed.
7. As the **admin**, press **Send Tomorrow's Reminders** and check the email log.
