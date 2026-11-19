# Demo accounts

Seeded automatically on first run of the backend.

| Role    | Email                  | Password   | Can do                                                        |
|---------|------------------------|------------|---------------------------------------------------------------|
| Admin   | admin@medivoc.com     | admin123   | Doctors, shifts, leaves, holidays, slot generation, full ledger |
| Doctor  | asha@medivoc.com      | doctor123  | Own slots, block/open slots, emergency cancellation            |
| Doctor  | raghav@medivoc.com    | doctor123  | same (Cardiology)                                              |
| Doctor  | nila@medivoc.com      | doctor123  | same (Dermatology)                                             |
| Doctor  | karan@medivoc.com     | doctor123  | same (Orthopedics)                                             |
| Doctor  | farah@medivoc.com     | doctor123  | same (Pediatrics)                                              |
| Patient | patient@medivoc.com   | patient123 | Form booking, voice booking, my appointments, reschedule       |

New patients can register themselves from the portal. New doctors are created by
an admin, which also creates their login.

Emails are not delivered unless SMTP is configured in `.env`; look in
`backend/data/sent_emails.log` to read what would have been sent.
