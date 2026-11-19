import { useEffect, useState } from 'react'
import { WEEKDAYS, api, today, type User } from '../api'
import './Panels.css'

type Doctor = { id: number; name: string; department: string; title: string; location: string; experience_years: number }
type Shift = { id: number; doctor_id: number; doctor_name: string; weekday: number; weekday_name: string; start_time: string; end_time: string; slot_minutes: number }
type Leave = { id: number; doctor_id: number; doctor_name: string; date: string; reason: string }
type Holiday = { id: number; date: string; name: string }
type Appointment = {
  id: number; reference_no: string; patient_name: string; patient_age: number; patient_gender: string
  patient_email: string; phone: string; reason: string; status: string; cancel_reason: string
  doctor_name: string; department: string; location: string; date: string; start_time: string; end_time: string
}
type Overview = Record<string, number>

type Tab = 'overview' | 'shifts' | 'leaves' | 'holidays' | 'ledger' | 'doctors'

const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'shifts', label: 'Doctor Shifts' },
  { key: 'leaves', label: 'Doctor Leaves' },
  { key: 'holidays', label: 'Hospital Holidays' },
  { key: 'ledger', label: 'Appointment Ledger' },
  { key: 'doctors', label: 'Add Doctor' },
]

export default function AdminPanel({ user }: { user: User }) {
  const [tab, setTab] = useState<Tab>('overview')
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [shifts, setShifts] = useState<Shift[]>([])
  const [leaves, setLeaves] = useState<Leave[]>([])
  const [holidays, setHolidays] = useState<Holiday[]>([])
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [overview, setOverview] = useState<Overview>({})
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  // form state
  const [shiftForm, setShiftForm] = useState({ doctor_id: '', weekday: '0', start_time: '09:00', end_time: '12:00', slot_minutes: '20' })
  const [leaveForm, setLeaveForm] = useState({ doctor_id: '', date: today(), reason: '' })
  const [holidayForm, setHolidayForm] = useState({ date: today(), name: '' })
  const [doctorForm, setDoctorForm] = useState({
    name: '', department: '', title: '', location: '', experience_years: '5', email: '', password: '',
  })
  const [generateDays, setGenerateDays] = useState('21')

  async function loadAll() {
    try {
      const [doctorList, shiftList, leaveList, holidayList, ledger, stats] = await Promise.all([
        api<Doctor[]>('/doctors'),
        api<Shift[]>('/admin/shifts'),
        api<Leave[]>('/admin/leaves'),
        api<Holiday[]>('/admin/holidays'),
        api<Appointment[]>('/appointments'),
        api<Overview>('/admin/overview'),
      ])
      setDoctors(doctorList)
      setShifts(shiftList)
      setLeaves(leaveList)
      setHolidays(holidayList)
      setAppointments(ledger)
      setOverview(stats)
    } catch (error) {
      setMessage({ type: 'error', text: error instanceof Error ? error.message : 'Could not load admin data.' })
    }
  }

  useEffect(() => {
    loadAll()
  }, [])

  /** Runs one write request, then refreshes everything and reports the outcome. */
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

  return (
    <div className="staff-shell">
      <header className="staff-header">
        <div>
          <span className="section-eyebrow">HOSPITAL ADMINISTRATION</span>
          <h2>Scheduling Control Desk</h2>
          <p>Signed in as {user.name} — manage shifts, leaves, holidays and the OPD calendar.</p>
        </div>
        <div className="staff-quick-action">
          <label htmlFor="gen-days">Open slots for next</label>
          <input
            id="gen-days"
            type="number"
            min={1}
            max={120}
            value={generateDays}
            onChange={(e) => setGenerateDays(e.target.value)}
          />
          <span>days</span>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run('Slot generation', () =>
                api('/admin/generate-slots', { method: 'POST', body: { days: Number(generateDays) } }).then((r: any) => ({
                  message: `${r.created_slots} new slots created from ${r.from} to ${r.to}.`,
                })),
              )
            }
          >
            Generate Slots
          </button>
        </div>
      </header>

      {message && (
        <div className={`portal-alert alert-${message.type === 'success' ? 'success' : 'error'}`} role="status">
          <span>{message.text}</span>
          <button type="button" className="alert-dismiss" onClick={() => setMessage(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}

      <nav className="staff-tabs" aria-label="Admin sections">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            className={tab === item.key ? 'active' : ''}
            onClick={() => setTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {tab === 'overview' && (
        <section className="panel-card">
          <h3>Hospital at a glance</h3>
          <div className="stat-grid">
            {[
              ['Doctors', overview.doctors],
              ['Registered Patients', overview.patients],
              ['Available Slots', overview.available_slots],
              ['Booked Slots', overview.booked_slots],
              ['Blocked Slots', overview.blocked_slots],
              ['Confirmed Appointments', overview.confirmed_appointments],
              ['Completed Consultations', overview.completed_consultations],
              ['No-shows', overview.no_shows],
              ['Doctor Cancellations', overview.doctor_cancellations],
              ['Weekly Shifts', overview.shifts],
              ['Leave Records', overview.leaves],
              ['Holidays', overview.holidays],
            ].map(([label, value]) => (
              <div className="stat-box" key={String(label)}>
                <span className="stat-number">{value ?? 0}</span>
                <span className="stat-label">{label}</span>
              </div>
            ))}
          </div>
          <p className="panel-hint">
            Slots are generated from doctor shifts. Holidays and doctor leave days are skipped, and any
            appointment already booked on a new holiday or leave day is cancelled with an email to the patient.
          </p>

          <div className="inline-form">
            <button
              type="button"
              disabled={busy}
              onClick={() => run('Reminders', () => api('/admin/send-reminders', { method: 'POST', body: { days_ahead: 1 } }))}
            >
              Send Tomorrow's Reminders
            </button>
            <p className="panel-hint grow">
              The server already does this every hour and each patient is reminded only once. Use this
              button to send the day-before reminder emails immediately.
            </p>
          </div>
        </section>
      )}

      {tab === 'shifts' && (
        <section className="panel-card">
          <h3>Weekly consultation shifts</h3>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              run('Shift', () =>
                api('/admin/shifts', {
                  method: 'POST',
                  body: {
                    doctor_id: Number(shiftForm.doctor_id),
                    weekday: Number(shiftForm.weekday),
                    start_time: shiftForm.start_time,
                    end_time: shiftForm.end_time,
                    slot_minutes: Number(shiftForm.slot_minutes),
                  },
                }),
              )
            }}
          >
            <label>
              Doctor
              <select
                required
                value={shiftForm.doctor_id}
                onChange={(e) => setShiftForm({ ...shiftForm, doctor_id: e.target.value })}
              >
                <option value="">Select doctor</option>
                {doctors.map((doctor) => (
                  <option key={doctor.id} value={doctor.id}>
                    {doctor.name} — {doctor.department}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Day
              <select value={shiftForm.weekday} onChange={(e) => setShiftForm({ ...shiftForm, weekday: e.target.value })}>
                {WEEKDAYS.map((day, index) => (
                  <option key={day} value={index}>
                    {day}
                  </option>
                ))}
              </select>
            </label>
            <label>
              From
              <input type="time" required value={shiftForm.start_time} onChange={(e) => setShiftForm({ ...shiftForm, start_time: e.target.value })} />
            </label>
            <label>
              To
              <input type="time" required value={shiftForm.end_time} onChange={(e) => setShiftForm({ ...shiftForm, end_time: e.target.value })} />
            </label>
            <label>
              Minutes per patient
              <input type="number" min={5} max={120} required value={shiftForm.slot_minutes} onChange={(e) => setShiftForm({ ...shiftForm, slot_minutes: e.target.value })} />
            </label>
            <button type="submit" disabled={busy}>Add Shift</button>
          </form>

          <div className="hospital-table-wrapper">
            <table className="hospital-table">
              <thead>
                <tr>
                  <th>Doctor</th>
                  <th>Day</th>
                  <th>Timing</th>
                  <th>Slot Length</th>
                  <th className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {shifts.length === 0 ? (
                  <tr><td colSpan={5} className="empty-table-state">No shifts defined yet.</td></tr>
                ) : (
                  shifts.map((shift) => (
                    <tr key={shift.id}>
                      <td><strong>{shift.doctor_name}</strong></td>
                      <td>{shift.weekday_name}</td>
                      <td>{shift.start_time} – {shift.end_time}</td>
                      <td>{shift.slot_minutes} min</td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn-cancel-appt"
                          disabled={busy}
                          onClick={() => run('Shift removal', () => api(`/admin/shifts/${shift.id}`, { method: 'DELETE' }))}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'leaves' && (
        <section className="panel-card">
          <h3>Doctor leave</h3>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              run('Leave', () =>
                api('/admin/leaves', {
                  method: 'POST',
                  body: { doctor_id: Number(leaveForm.doctor_id), date: leaveForm.date, reason: leaveForm.reason },
                }),
              )
            }}
          >
            <label>
              Doctor
              <select required value={leaveForm.doctor_id} onChange={(e) => setLeaveForm({ ...leaveForm, doctor_id: e.target.value })}>
                <option value="">Select doctor</option>
                {doctors.map((doctor) => (
                  <option key={doctor.id} value={doctor.id}>{doctor.name}</option>
                ))}
              </select>
            </label>
            <label>
              Date
              <input type="date" required min={today()} value={leaveForm.date} onChange={(e) => setLeaveForm({ ...leaveForm, date: e.target.value })} />
            </label>
            <label className="grow">
              Reason
              <input type="text" placeholder="Conference, personal leave..." value={leaveForm.reason} onChange={(e) => setLeaveForm({ ...leaveForm, reason: e.target.value })} />
            </label>
            <button type="submit" disabled={busy}>Mark Leave</button>
          </form>

          <div className="hospital-table-wrapper">
            <table className="hospital-table">
              <thead>
                <tr><th>Doctor</th><th>Date</th><th>Reason</th><th className="text-right">Action</th></tr>
              </thead>
              <tbody>
                {leaves.length === 0 ? (
                  <tr><td colSpan={4} className="empty-table-state">No leave recorded.</td></tr>
                ) : (
                  leaves.map((leave) => (
                    <tr key={leave.id}>
                      <td><strong>{leave.doctor_name}</strong></td>
                      <td>{leave.date}</td>
                      <td>{leave.reason || '—'}</td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn-cancel-appt"
                          disabled={busy}
                          onClick={() => run('Leave removal', () => api(`/admin/leaves/${leave.id}`, { method: 'DELETE' }))}
                        >
                          Cancel Leave
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <p className="panel-hint">
            Marking leave clears that doctor's free slots for the day. Patients already booked are cancelled
            automatically and emailed to reschedule.
          </p>
        </section>
      )}

      {tab === 'holidays' && (
        <section className="panel-card">
          <h3>Hospital holidays</h3>
          <form
            className="inline-form"
            onSubmit={(e) => {
              e.preventDefault()
              run('Holiday', () => api('/admin/holidays', { method: 'POST', body: holidayForm }))
            }}
          >
            <label>
              Date
              <input type="date" required min={today()} value={holidayForm.date} onChange={(e) => setHolidayForm({ ...holidayForm, date: e.target.value })} />
            </label>
            <label className="grow">
              Occasion
              <input type="text" required placeholder="e.g. Republic Day" value={holidayForm.name} onChange={(e) => setHolidayForm({ ...holidayForm, name: e.target.value })} />
            </label>
            <button type="submit" disabled={busy}>Declare Holiday</button>
          </form>

          <div className="hospital-table-wrapper">
            <table className="hospital-table">
              <thead><tr><th>Date</th><th>Occasion</th><th className="text-right">Action</th></tr></thead>
              <tbody>
                {holidays.length === 0 ? (
                  <tr><td colSpan={3} className="empty-table-state">No holidays declared.</td></tr>
                ) : (
                  holidays.map((holiday) => (
                    <tr key={holiday.id}>
                      <td><strong>{holiday.date}</strong></td>
                      <td>{holiday.name}</td>
                      <td className="text-right">
                        <button
                          type="button"
                          className="btn-cancel-appt"
                          disabled={busy}
                          onClick={() => run('Holiday removal', () => api(`/admin/holidays/${holiday.id}`, { method: 'DELETE' }))}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'ledger' && (
        <section className="panel-card">
          <h3>Appointment ledger ({appointments.length})</h3>
          <div className="hospital-table-wrapper">
            <table className="hospital-table">
              <thead>
                <tr>
                  <th>Token</th><th>Patient</th><th>Contact</th><th>Consultant</th>
                  <th>Schedule</th><th>Status</th><th className="text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {appointments.length === 0 ? (
                  <tr><td colSpan={7} className="empty-table-state">No appointments booked yet.</td></tr>
                ) : (
                  appointments.map((appointment) => (
                    <tr key={appointment.id}>
                      <td><span className="token-ref">{appointment.reference_no}</span></td>
                      <td>
                        <strong>{appointment.patient_name}</strong>
                        <div className="table-patient-sub">{appointment.patient_age} yrs • {appointment.patient_gender}</div>
                        <div className="table-reason-snippet" title={appointment.reason}>"{appointment.reason}"</div>
                      </td>
                      <td>
                        <span className="phone-num">{appointment.phone}</span>
                        <div className="table-patient-sub">{appointment.patient_email}</div>
                      </td>
                      <td>
                        <div className="doctor-cell">
                          <strong>{appointment.doctor_name}</strong>
                          <span>{appointment.department}</span>
                        </div>
                      </td>
                      <td>
                        <div className="schedule-cell">
                          <strong>{appointment.date}</strong>
                          <span>{appointment.start_time} - {appointment.end_time}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`status-chip ${appointment.status === 'confirmed' ? 'confirmed' : 'cancelled'}`}>
                          {appointment.status.replace(/_/g, ' ')}
                        </span>
                        {appointment.cancel_reason && (
                          <div className="table-patient-sub">{appointment.cancel_reason}</div>
                        )}
                      </td>
                      <td className="text-right">
                        {appointment.status === 'confirmed' && (
                          <button
                            type="button"
                            className="btn-cancel-appt"
                            disabled={busy}
                            onClick={() => run('Cancellation', () => api(`/appointments/${appointment.id}`, { method: 'DELETE' }))}
                          >
                            Cancel & Release
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {tab === 'doctors' && (
        <section className="panel-card">
          <h3>Add a doctor</h3>
          <p className="panel-hint">This also creates the doctor's login account for the doctor desk.</p>
          <form
            className="stacked-form"
            onSubmit={(e) => {
              e.preventDefault()
              run('Doctor', () =>
                api('/admin/doctors', {
                  method: 'POST',
                  body: { ...doctorForm, experience_years: Number(doctorForm.experience_years) },
                }),
              )
            }}
          >
            <label>Name<input required value={doctorForm.name} onChange={(e) => setDoctorForm({ ...doctorForm, name: e.target.value })} placeholder="Dr. Meera Nair" /></label>
            <label>Department<input required value={doctorForm.department} onChange={(e) => setDoctorForm({ ...doctorForm, department: e.target.value })} placeholder="Neurology" /></label>
            <label>Designation<input required value={doctorForm.title} onChange={(e) => setDoctorForm({ ...doctorForm, title: e.target.value })} placeholder="Consultant Neurologist, DM" /></label>
            <label>Consultation Room<input required value={doctorForm.location} onChange={(e) => setDoctorForm({ ...doctorForm, location: e.target.value })} placeholder="OPD Block B, Room 210" /></label>
            <label>Experience (years)<input type="number" min={0} max={60} required value={doctorForm.experience_years} onChange={(e) => setDoctorForm({ ...doctorForm, experience_years: e.target.value })} /></label>
            <label>Login Email<input type="email" required value={doctorForm.email} onChange={(e) => setDoctorForm({ ...doctorForm, email: e.target.value })} placeholder="meera@medivoc.com" /></label>
            <label>Temporary Password<input type="password" minLength={6} required value={doctorForm.password} onChange={(e) => setDoctorForm({ ...doctorForm, password: e.target.value })} /></label>
            <button type="submit" className="btn-primary-wide" disabled={busy}>Create Doctor Account</button>
          </form>

          <div className="hospital-table-wrapper">
            <table className="hospital-table">
              <thead><tr><th>Doctor</th><th>Department</th><th>Designation</th><th>Room</th><th>Experience</th></tr></thead>
              <tbody>
                {doctors.map((doctor) => (
                  <tr key={doctor.id}>
                    <td><strong>{doctor.name}</strong></td>
                    <td>{doctor.department}</td>
                    <td>{doctor.title}</td>
                    <td>{doctor.location}</td>
                    <td>{doctor.experience_years} yrs</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
