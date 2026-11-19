import { useEffect, useState } from 'react'
import { api } from '../api'
import './Panels.css'

export type MyAppointment = {
  id: number
  reference_no: string
  patient_name: string
  patient_age: number
  patient_gender: string
  patient_email: string
  phone: string
  reason: string
  status: 'confirmed' | 'cancelled' | 'cancelled_by_doctor' | 'completed' | 'no_show'
  cancel_reason: string
  doctor_id: number
  doctor_name: string
  doctor_title: string
  department: string
  location: string
  slot_id: number
  date: string
  start_time: string
  end_time: string
}

type Slot = { id: number; doctor_id: number; date: string; start_time: string; end_time: string }

type Props = {
  /** Called after a cancel or reschedule so the booking form's slot list stays fresh. */
  onChanged: () => void
  reloadKey: number
}

const STATUS_LABEL: Record<MyAppointment['status'], string> = {
  confirmed: 'Confirmed',
  cancelled: 'Cancelled by you',
  cancelled_by_doctor: 'Cancelled by hospital',
  completed: 'Consultation completed',
  no_show: 'Marked as not attended',
}

/** Which .status-chip colour each status wears. */
const STATUS_CHIP: Record<MyAppointment['status'], string> = {
  confirmed: 'confirmed',
  cancelled: 'cancelled',
  cancelled_by_doctor: 'cancelled',
  completed: 'completed',
  no_show: 'no-show',
}

export default function MyAppointments({ onChanged, reloadKey }: Props) {
  const [appointments, setAppointments] = useState<MyAppointment[]>([])
  const [rescheduling, setRescheduling] = useState<MyAppointment | null>(null)
  const [options, setOptions] = useState<Slot[]>([])
  const [chosenSlot, setChosenSlot] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  async function load() {
    try {
      setAppointments(await api<MyAppointment[]>('/me/appointments'))
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not load your appointments.' })
    }
  }

  useEffect(() => {
    load()
  }, [reloadKey])

  async function openReschedule(appointment: MyAppointment) {
    setRescheduling(appointment)
    setChosenSlot('')
    setMessage(null)
    try {
      setOptions(await api<Slot[]>(`/slots?doctor_id=${appointment.doctor_id}`))
    } catch {
      setOptions([])
    }
  }

  async function run(label: string, request: () => Promise<any>) {
    setBusy(true)
    setMessage(null)
    try {
      const result = await request()
      setMessage({ type: 'success', text: result?.message ?? `${label} done.` })
      setRescheduling(null)
      await load()
      onChanged()
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : `${label} failed.` })
    } finally {
      setBusy(false)
    }
  }

  function cancel(appointment: MyAppointment) {
    const ok = window.confirm(
      `Cancel appointment ${appointment.reference_no} with ${appointment.doctor_name}? The slot will be offered to other patients.`,
    )
    if (!ok) return
    run('Cancellation', () => api(`/appointments/${appointment.id}`, { method: 'DELETE' }))
  }

  return (
    <section className="portal-container section-container" id="my-appointments">
      <div className="section-header-block">
        <span className="section-eyebrow">MY RECORDS</span>
        <h2>My Appointments</h2>
        <p>
          Every booking made by form or by the Voice AI desk appears here. A confirmation email is sent to your
          registered address for each one.
        </p>
      </div>

      {message && (
        <div className={`portal-alert alert-${message.type === 'success' ? 'success' : 'error'}`} role="status">
          <span>{message.text}</span>
          <button type="button" className="alert-dismiss" onClick={() => setMessage(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      {appointments.length === 0 ? (
        <p className="empty-table-state">
          No appointments yet. Use the booking form above or the Voice AI desk at the bottom right.
        </p>
      ) : (
        <div className="appointment-list">
          {appointments.map((appointment) => (
            <article className={`appointment-row ${appointment.status}`} key={appointment.id}>
              <div className="appointment-main">
                <div className="appointment-token">
                  <span className="token-ref">{appointment.reference_no}</span>
                  <span className={`status-chip ${STATUS_CHIP[appointment.status]}`}>
                    {STATUS_LABEL[appointment.status]}
                  </span>
                </div>
                <h3>{appointment.doctor_name}</h3>
                <p className="appointment-sub">{appointment.doctor_title} • {appointment.department}</p>
                <p className="appointment-sub">
                  <strong>{appointment.date}</strong> at {appointment.start_time} – {appointment.end_time} • {appointment.location}
                </p>
                <p className="appointment-sub">Reason: {appointment.reason}</p>
                {appointment.cancel_reason && (
                  <p className="appointment-alert">Hospital note: {appointment.cancel_reason}</p>
                )}
                {appointment.status === 'completed' && (
                  <p className="appointment-sub muted">
                    This consultation is closed. Book a new appointment for a follow-up visit.
                  </p>
                )}
                {appointment.status === 'no_show' && (
                  <p className="appointment-alert">
                    You were not marked present for this consultation. Please book again if you
                    still need to be seen.
                  </p>
                )}
              </div>

              <div className="appointment-actions">
                {appointment.status === 'confirmed' && (
                  <>
                    <button type="button" className="btn-soft" disabled={busy} onClick={() => openReschedule(appointment)}>
                      Reschedule
                    </button>
                    <button type="button" className="btn-cancel-appt" disabled={busy} onClick={() => cancel(appointment)}>
                      Cancel
                    </button>
                  </>
                )}
                {appointment.status === 'cancelled_by_doctor' && (
                  <button type="button" className="btn-soft highlight" disabled={busy} onClick={() => openReschedule(appointment)}>
                    Reschedule now
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {rescheduling && (
        <div className="reschedule-box">
          <h3>Reschedule {rescheduling.reference_no}</h3>
          <p className="panel-hint">
            Pick another free slot with {rescheduling.doctor_name}. Your token number stays the same and a new
            confirmation email is sent.
          </p>
          <div className="inline-form">
            <label className="grow">
              Available slots
              <select value={chosenSlot} onChange={(e) => setChosenSlot(e.target.value)}>
                <option value="">Select a slot</option>
                {options.map((slot) => (
                  <option key={slot.id} value={slot.id}>
                    {slot.date} • {slot.start_time} – {slot.end_time}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              disabled={busy || !chosenSlot}
              onClick={() =>
                run('Reschedule', () =>
                  api(`/appointments/${rescheduling.id}/reschedule`, {
                    method: 'POST',
                    body: { slot_id: Number(chosenSlot) },
                  }),
                )
              }
            >
              Confirm New Slot
            </button>
            <button type="button" className="btn-soft" onClick={() => setRescheduling(null)}>
              Close
            </button>
          </div>
          {options.length === 0 && (
            <p className="appointment-alert">
              This doctor has no free slots right now. Please book with another specialist from the form above.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
