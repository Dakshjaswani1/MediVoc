import { useEffect, useMemo, useState } from 'react'
import { API_URL, WEEKDAYS, api, today, type User } from '../api'
import VoiceAssistant from './VoiceAssistant'
import './Panels.css'

type DoctorSlot = {
  id: number
  date: string
  start_time: string
  end_time: string
  status: 'available' | 'booked' | 'blocked'
  appointment_id: number | null
  /** Status of the appointment in this slot, once one has ever been booked into it. */
  appointment_status: 'confirmed' | 'completed' | 'no_show' | null
  patient_name: string | null
  patient_age: number | null
  patient_gender: string | null
  phone: string | null
  patient_email: string | null
  reason: string | null
}

type Profile = {
  doctor: { id: number; name: string; department: string; title: string; location: string; experience_years: number }
  shifts: { id: number; weekday: number; start_time: string; end_time: string; slot_minutes: number }[]
  leaves: { id: number; date: string; reason: string }[]
  holidays: { id: number; date: string; name: string }[]
  slot_counts: { available: number; booked: number; blocked: number }
}

const OUTCOME_LABEL: Record<'completed' | 'no_show', string> = {
  completed: 'Attended',
  no_show: 'No-show',
}

type Filter = 'all' | 'available' | 'booked' | 'blocked'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All Slots' },
  { key: 'booked', label: 'Booked' },
  { key: 'available', label: 'Available' },
  { key: 'blocked', label: 'Blocked' },
]

export default function DoctorPanel({ user }: { user: User }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [slots, setSlots] = useState<DoctorSlot[]>([])
  const [filter, setFilter] = useState<Filter>('booked')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [extraSlot, setExtraSlot] = useState({ date: today(), start_time: '18:00', end_time: '18:20' })

  async function loadAll() {
    try {
      const [profileData, slotData] = await Promise.all([
        api<Profile>('/doctor/profile'),
        api<DoctorSlot[]>('/doctor/slots'),
      ])
      setProfile(profileData)
      setSlots(slotData)
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not load your calendar.' })
    }
  }

  useEffect(() => {
    loadAll()
  }, [])

  async function run(label: string, request: () => Promise<any>) {
    setBusy(true)
    setMessage(null)
    try {
      const result = await request()
      setMessage({ type: 'success', text: result?.message ?? `${label} done.` })
      await loadAll()
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : `${label} failed.` })
    } finally {
      setBusy(false)
    }
  }

  function recordOutcome(slot: DoctorSlot, status: 'completed' | 'no_show') {
    const verb = status === 'completed' ? 'attended' : 'did not attend'
    if (!window.confirm(`Mark ${slot.patient_name} (${slot.date} ${slot.start_time}) as ${verb}?`)) return
    run('Outcome', () =>
      api(`/doctor/appointments/${slot.appointment_id}/outcome`, { method: 'POST', body: { status } }),
    )
  }

  function cancelConsultation(slot: DoctorSlot) {
    const reason = window.prompt(
      `Cancel the consultation with ${slot.patient_name} on ${slot.date} at ${slot.start_time}?\n\n` +
        'The patient will receive an email asking them to reschedule.\n\nReason:',
      'Doctor unavailable due to an emergency',
    )
    if (reason === null) return
    run('Cancellation', () =>
      api(`/doctor/appointments/${slot.appointment_id}/cancel`, { method: 'POST', body: { reason } }),
    )
  }

  const visible = useMemo(
    () => (filter === 'all' ? slots : slots.filter((slot) => slot.status === filter)),
    [slots, filter],
  )

  const grouped = useMemo(() => {
    const map = new Map<string, DoctorSlot[]>()
    for (const slot of visible) {
      map.set(slot.date, [...(map.get(slot.date) ?? []), slot])
    }
    return [...map.entries()]
  }, [visible])

  return (
    <div className="staff-shell">
      <header className="staff-header">
        <div>
          <span className="section-eyebrow">DOCTOR DESK</span>
          <h2>{profile?.doctor.name ?? user.name}</h2>
          <p>
            {profile ? `${profile.doctor.title} • ${profile.doctor.department} • ${profile.doctor.location}` : 'Loading your profile...'}
          </p>
        </div>
        {profile && (
          <div className="stat-grid compact">
            <div className="stat-box"><span className="stat-number">{profile.slot_counts.booked}</span><span className="stat-label">Booked</span></div>
            <div className="stat-box"><span className="stat-number">{profile.slot_counts.available}</span><span className="stat-label">Available</span></div>
            <div className="stat-box"><span className="stat-number">{profile.slot_counts.blocked}</span><span className="stat-label">Blocked</span></div>
          </div>
        )}
      </header>

      {message && (
        <div className={`portal-alert alert-${message.type === 'success' ? 'success' : 'error'}`} role="status">
          <span>{message.text}</span>
          <button type="button" className="alert-dismiss" onClick={() => setMessage(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <section className="panel-card">
        <div className="panel-card-head">
          <h3>My consultation calendar</h3>
          <div className="staff-tabs tight">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={filter === item.key ? 'active' : ''}
                onClick={() => setFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {grouped.length === 0 ? (
          <p className="empty-table-state">No slots in this view.</p>
        ) : (
          grouped.map(([date, daySlots]) => (
            <div className="day-group" key={date}>
              <h4>
                {date} <small>{WEEKDAYS[(new Date(`${date}T00:00:00`).getDay() + 6) % 7]}</small>
              </h4>
              <div className="slot-grid">
                {daySlots.map((slot) => (
                  <article className={`slot-card ${slot.status}`} key={slot.id}>
                    <div className="slot-time">
                      {slot.start_time} – {slot.end_time}
                      <span className={`status-chip ${slot.status === 'booked' ? 'confirmed' : slot.status}`}>
                        {slot.status}
                      </span>
                    </div>

                    {slot.status === 'booked' && (
                      <div className="slot-patient">
                        <strong>{slot.patient_name}</strong>
                        <span>{slot.patient_age} yrs • {slot.patient_gender}</span>
                        <span>{slot.phone}</span>
                        <em title={slot.reason ?? ''}>"{slot.reason}"</em>
                        {slot.appointment_status && slot.appointment_status !== 'confirmed' && (
                          <span className={`status-chip ${slot.appointment_status === 'completed' ? 'completed' : 'no-show'}`}>
                            {OUTCOME_LABEL[slot.appointment_status]}
                          </span>
                        )}
                      </div>
                    )}

                    <div className="slot-actions">
                      {slot.status === 'booked' && slot.appointment_status === 'confirmed' && slot.date <= today() && (
                        <>
                          <button type="button" className="btn-soft highlight" disabled={busy} onClick={() => recordOutcome(slot, 'completed')}>
                            Mark attended
                          </button>
                          <button type="button" className="btn-soft" disabled={busy} onClick={() => recordOutcome(slot, 'no_show')}>
                            No-show
                          </button>
                        </>
                      )}
                      {slot.status === 'booked' && slot.appointment_status === 'confirmed' && (
                        <button type="button" className="btn-cancel-appt" disabled={busy} onClick={() => cancelConsultation(slot)}>
                          Cancel (emergency)
                        </button>
                      )}
                      {slot.status === 'available' && (
                        <button
                          type="button"
                          className="btn-soft"
                          disabled={busy}
                          onClick={() => run('Block', () => api(`/doctor/slots/${slot.id}`, { method: 'PATCH', body: { status: 'blocked' } }))}
                        >
                          Block
                        </button>
                      )}
                      {slot.status === 'blocked' && (
                        <button
                          type="button"
                          className="btn-soft"
                          disabled={busy}
                          onClick={() => run('Re-open', () => api(`/doctor/slots/${slot.id}`, { method: 'PATCH', body: { status: 'available' } }))}
                        >
                          Re-open
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ))
        )}
      </section>

      <section className="panel-card">
        <h3>Open an extra slot</h3>
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault()
            run('Extra slot', () => api('/doctor/slots', { method: 'POST', body: extraSlot }))
          }}
        >
          <label>
            Date
            <input type="date" required min={today()} value={extraSlot.date} onChange={(e) => setExtraSlot({ ...extraSlot, date: e.target.value })} />
          </label>
          <label>
            From
            <input type="time" required value={extraSlot.start_time} onChange={(e) => setExtraSlot({ ...extraSlot, start_time: e.target.value })} />
          </label>
          <label>
            To
            <input type="time" required value={extraSlot.end_time} onChange={(e) => setExtraSlot({ ...extraSlot, end_time: e.target.value })} />
          </label>
          <button type="submit" disabled={busy}>Add Slot</button>
        </form>
      </section>

      {profile && (
        <section className="panel-card">
          <h3>My roster</h3>
          <div className="roster-grid">
            <div>
              <h4>Weekly shifts</h4>
              {profile.shifts.length === 0 ? (
                <p className="panel-hint">No shifts assigned. Ask the administrator to add one.</p>
              ) : (
                <ul className="plain-list">
                  {profile.shifts.map((shift) => (
                    <li key={shift.id}>
                      <strong>{WEEKDAYS[shift.weekday]}</strong> {shift.start_time} – {shift.end_time} ({shift.slot_minutes} min each)
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h4>My approved leave</h4>
              {profile.leaves.length === 0 ? (
                <p className="panel-hint">No leave recorded.</p>
              ) : (
                <ul className="plain-list">
                  {profile.leaves.map((leave) => (
                    <li key={leave.id}><strong>{leave.date}</strong> {leave.reason || ''}</li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h4>Upcoming hospital holidays</h4>
              {profile.holidays.length === 0 ? (
                <p className="panel-hint">None scheduled.</p>
              ) : (
                <ul className="plain-list">
                  {profile.holidays.map((holiday) => (
                    <li key={holiday.id}><strong>{holiday.date}</strong> {holiday.name}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>
      )}

      <VoiceAssistant apiUrl={API_URL} role="doctor" onChanged={loadAll} />
    </div>
  )
}
