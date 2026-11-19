import { type FormEvent, useEffect, useMemo, useState } from 'react'
import './App.css'
import { API_URL, api, clearSession, savedUser, type User } from './api'
import AdminPanel from './components/AdminPanel'
import AuthScreen from './components/AuthScreen'
import DoctorPanel from './components/DoctorPanel'
import MyAppointments from './components/MyAppointments'
import VoiceAssistant from './components/VoiceAssistant'

type Doctor = {
  id: number
  name: string
  department: string
  title: string
  location: string
  experience_years: number
}

type Slot = {
  id: number
  doctor_id: number
  date: string
  start_time: string
  end_time: string
  status: 'available' | 'booked'
}

type Appointment = {
  id: number
  reference_no?: string
  patient_name: string
  patient_age: number
  patient_gender?: string
  phone: string
  reason: string
  doctor_id: number
  doctor_name: string
  department: string
  doctor_title?: string
  location?: string
  slot_id: number
  date: string
  start_time: string
  end_time?: string
  status: string
  created_at?: string
}

type BookingForm = {
  department: string
  doctorId: string
  slotId: string
  patientName: string
  patientAge: string
  patientGender: string
  phone: string
  reason: string
  ackNonEmergency: boolean
}

const initialForm: BookingForm = {
  department: '',
  doctorId: '',
  slotId: '',
  patientName: '',
  patientAge: '',
  patientGender: 'Male',
  phone: '',
  reason: '',
  ackNonEmergency: false,
}

/** Decides which portal the signed-in role sees. */
export default function App() {
  const [user, setUser] = useState<User | null>(savedUser())

  function signOut() {
    clearSession()
    setUser(null)
  }

  if (!user) {
    return <AuthScreen onSignedIn={setUser} />
  }

  return (
    <>
      <div className="session-bar">
        <div className="session-bar-inner">
          <div className="session-identity">
            <span className="role-chip">{user.role}</span>
            <strong>{user.name}</strong>
            <span>{user.email}</span>
          </div>
          <button type="button" className="btn-signout" onClick={signOut}>
            Sign Out
          </button>
        </div>
      </div>

      {user.role === 'admin' && <AdminPanel user={user} />}
      {user.role === 'doctor' && <DoctorPanel user={user} />}
      {user.role === 'patient' && <PatientPortal user={user} />}
    </>
  )
}

function PatientPortal({ user }: { user: User }) {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [slots, setSlots] = useState<Slot[]>([])
  const [form, setForm] = useState<BookingForm>({
    ...initialForm,
    patientName: user.name,
    phone: user.phone,
  })
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [confirmedSlip, setConfirmedSlip] = useState<Appointment | null>(null)
  // Bumped after every booking so "My Appointments" reloads itself.
  const [recordsKey, setRecordsKey] = useState(0)
  const [notification, setNotification] = useState<{
    type: 'success' | 'error' | 'info'
    message: string
  } | null>(null)

  const departments = useMemo(
    () => Array.from(new Set(doctors.map((doctor) => doctor.department))),
    [doctors],
  )

  const filteredDoctors = useMemo(
    () =>
      form.department
        ? doctors.filter((doctor) => doctor.department === form.department)
        : doctors,
    [doctors, form.department],
  )

  const availableSlots = useMemo(
    () =>
      slots.filter(
        (slot) =>
          slot.doctor_id === Number(form.doctorId) && slot.status === 'available',
      ),
    [slots, form.doctorId],
  )

  const selectedDoctor = doctors.find((doc) => doc.id === Number(form.doctorId))
  const selectedSlot = slots.find((s) => s.id === Number(form.slotId))

  async function loadData() {
    try {
      const [docsData, slotsData] = await Promise.all([
        api<Doctor[]>('/doctors'),
        api<Slot[]>('/slots'),
      ])
      setDoctors(docsData)
      setSlots(slotsData)
      setRecordsKey((key) => key + 1)
    } catch {
      setNotification({
        type: 'error',
        message:
          'Unable to connect to MediVoc Hospital Appointment Service. Please ensure the backend is running.',
      })
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  function handleSelectDoctor(doctor: Doctor) {
    setForm((prev) => ({
      ...prev,
      department: doctor.department,
      doctorId: String(doctor.id),
      slotId: '',
    }))
    setConfirmedSlip(null)
    const formElement = document.getElementById('booking-portal')
    if (formElement) {
      formElement.scrollIntoView({ behavior: 'smooth' })
    }
  }

  function updateFormField<K extends keyof BookingForm>(field: K, value: BookingForm[K]) {
    setNotification(null)
    setForm((current) => {
      const next = { ...current, [field]: value }

      if (field === 'department') {
        next.doctorId = ''
        next.slotId = ''
      }

      if (field === 'doctorId') {
        next.slotId = ''
      }

      return next
    })
  }

  async function handleSubmitBooking(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!form.ackNonEmergency) {
      setNotification({
        type: 'error',
        message: 'Please confirm that this appointment is for non-urgent outpatient care.',
      })
      return
    }

    if (!form.doctorId || !form.slotId) {
      setNotification({
        type: 'error',
        message: 'Please select a consulting specialist and an available consultation slot.',
      })
      return
    }

    setIsSubmitting(true)
    setNotification(null)

    try {
      const created = await api<Appointment>('/appointments', {
        method: 'POST',
        body: {
          patient_name: form.patientName.trim(),
          patient_age: Number(form.patientAge),
          patient_gender: form.patientGender,
          phone: form.phone.trim(),
          reason: form.reason.trim(),
          doctor_id: Number(form.doctorId),
          slot_id: Number(form.slotId),
        },
      })

      setConfirmedSlip(created)
      setNotification({
        type: 'success',
        message: `Consultation token ${created.reference_no ?? `MV-2026-${created.id}`} generated. A confirmation email has been sent to ${user.email}.`,
      })
      setForm({ ...initialForm, patientName: user.name, phone: user.phone })
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Error processing appointment.'
      setNotification({ type: 'error', message: msg })
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleVoiceAppointmentBooked(newAppt: Appointment) {
    setConfirmedSlip(newAppt)
    setNotification({
      type: 'success',
      message: `Voice AI Assistant successfully scheduled consultation token ${newAppt.reference_no ?? `MV-2026-${newAppt.id}`}!`,
    })
    loadData()
    const slipElement = document.querySelector('.confirmed-slip-section')
    if (slipElement) {
      slipElement.scrollIntoView({ behavior: 'smooth' })
    }
  }

  return (
    <div className="hospital-portal">
      {/* 1. TOP EMERGENCY & HELPLINE BAR */}
      <aside className="emergency-strip" aria-label="Hospital emergency helpline">
        <div className="portal-container emergency-strip-inner">
          <div className="emergency-contact">
            <span className="emergency-badge">24/7 CASUALTY & TRAUMA</span>
            <strong>Emergency: 108 / +91 (80) 4000-1122</strong>
          </div>
          <div className="helpline-items">
            <span>Ambulance: +91 (80) 4000-1199</span>
            <span className="divider">|</span>
            <span>OPD Desk: (080) 4000-1000 (Mon–Sat, 8:00 AM – 6:00 PM)</span>
            <span className="divider">|</span>
            <span>Metro Central Campus</span>
          </div>
        </div>
      </aside>

      {/* 2. PRIMARY INSTITUTIONAL HEADER */}
      <header className="hospital-header">
        <div className="portal-container header-inner">
          <div className="hospital-brand">
            <div className="hospital-seal" aria-hidden="true">
              <svg
                width="32"
                height="32"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 2v20" />
                <path d="M2 12h20" />
              </svg>
            </div>
            <div>
              <h1 className="hospital-name">MediVoc Memorial Hospital</h1>
              <p className="hospital-sub">
                Outpatient Department (OPD) & Specialist Consultation Centre
              </p>
            </div>
          </div>

          <nav className="hospital-nav" aria-label="Portal Navigation">
            <a href="#booking-portal" className="nav-link">
              Schedule Consultation
            </a>
            <a href="#specialists-directory" className="nav-link">
              Medical Faculty
            </a>
            <a href="#my-appointments" className="nav-link">
              My Appointments
            </a>
            <a href="#patient-guidelines" className="nav-link">
              Patient Guidelines
            </a>
          </nav>
        </div>
      </header>

      {/* 3. CLINICAL TRIAGE NOTICE BANNER */}
      <div className="triage-banner" role="alert">
        <div className="portal-container triage-inner">
          <svg
            className="triage-icon"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <div className="triage-copy">
            <strong>Clinical Safety Advisory:</strong>
            <span>
              {' '}
              This portal is strictly for scheduled outpatient consultations. If
              the patient is experiencing acute chest pain, severe breathlessness,
              sudden loss of speech/motor function, heavy hemorrhage, or traumatic
              injury, report immediately to Casualty (Ground Floor, Block E) or dial
              108.
            </span>
          </div>
        </div>
      </div>

      {/* 4. HOSPITAL CREDENTIALS BAR */}
      <section className="credentials-bar" aria-label="Hospital accreditation">
        <div className="portal-container credentials-grid">
          <div className="credential-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            <div>
              <strong>NABH & JCI Accredited</strong>
              <small>National Healthcare Standards</small>
            </div>
          </div>
          <div className="credential-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
            </svg>
            <div>
              <strong>24x7 Critical Care & ICU</strong>
              <small>Full Emergency Readiness</small>
            </div>
          </div>
          <div className="credential-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            <div>
              <strong>Confirmed Time Slots</strong>
              <small>Reduced Waiting Room Times</small>
            </div>
          </div>
          <div className="credential-item">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
            <div>
              <strong>Centralized EHR Database</strong>
              <small>Prescriptions & Records Linked</small>
            </div>
          </div>
        </div>
      </section>

      {/* NOTIFICATIONS CONTAINER */}
      {notification && (
        <div className="portal-container">
          <div className={`portal-alert alert-${notification.type}`} role="status">
            <span>{notification.message}</span>
            <button
              type="button"
              className="alert-dismiss"
              onClick={() => setNotification(null)}
              aria-label="Dismiss alert"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {/* 5. CONFIRMED APPOINTMENT SLIP (PRINTABLE RECEIPT) */}
      {confirmedSlip && (
        <section className="portal-container confirmed-slip-section" aria-label="Appointment Confirmation Receipt">
          <div className="appointment-slip">
            <div className="slip-header">
              <div className="slip-hospital-meta">
                <div className="hospital-seal-small" aria-hidden="true">+</div>
                <div>
                  <h3>MediVoc Memorial Hospital</h3>
                  <p>Official Outpatient Consultation Token</p>
                </div>
              </div>
              <div className="slip-token-badge">
                <small>TOKEN NUMBER</small>
                <strong>{confirmedSlip.reference_no ?? `MV-2026-${confirmedSlip.id}`}</strong>
                <span className="status-chip confirmed">CONFIRMED</span>
              </div>
            </div>

            <div className="slip-body">
              <div className="slip-details-grid">
                <div className="slip-field">
                  <label>Patient Name</label>
                  <span>{confirmedSlip.patient_name}</span>
                </div>
                <div className="slip-field">
                  <label>Age & Gender</label>
                  <span>
                    {confirmedSlip.patient_age} yrs / {confirmedSlip.patient_gender ?? 'Not specified'}
                  </span>
                </div>
                <div className="slip-field">
                  <label>Contact Phone</label>
                  <span>{confirmedSlip.phone}</span>
                </div>
                <div className="slip-field">
                  <label>Clinical Department</label>
                  <span>{confirmedSlip.department}</span>
                </div>
                <div className="slip-field">
                  <label>Consulting Specialist</label>
                  <strong>{confirmedSlip.doctor_name}</strong>
                  <small>{confirmedSlip.doctor_title}</small>
                </div>
                <div className="slip-field">
                  <label>Consultation Location</label>
                  <span>{confirmedSlip.location ?? 'OPD Main Block'}</span>
                </div>
                <div className="slip-field">
                  <label>Appointment Date</label>
                  <strong>{confirmedSlip.date}</strong>
                </div>
                <div className="slip-field">
                  <label>Consultation Window</label>
                  <strong>
                    {confirmedSlip.start_time} {confirmedSlip.end_time ? `to ${confirmedSlip.end_time}` : ''}
                  </strong>
                </div>
              </div>

              <div className="slip-instructions">
                <strong>Reporting Instructions for Patient:</strong>
                <ul>
                  <li>
                    Please arrive at the designated OPD registration counter at least <strong>15 minutes prior</strong> to your scheduled time.
                  </li>
                  <li>Present this Token ID (or registered mobile number) at Reception Desk Counter 2.</li>
                  <li>Carry your previous medical history, prescriptions, diagnostic test reports, and a government photo ID.</li>
                </ul>
              </div>
            </div>

            <div className="slip-actions no-print">
              <button
                type="button"
                className="btn-primary"
                onClick={() => window.print()}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="6 9 6 2 18 2 18 9" />
                  <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
                  <rect x="6" y="14" width="12" height="8" />
                </svg>
                Print / Save Appointment Slip
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmedSlip(null)}
              >
                Close Receipt
              </button>
            </div>
          </div>
        </section>
      )}

      {/* 6. OUTPATIENT BOOKING DESK */}
      <main className="portal-container main-grid" id="booking-portal">
        <section className="booking-card" aria-labelledby="form-heading">
          <div className="card-header">
            <span className="section-eyebrow">OPD REGISTRATION</span>
            <h2 id="form-heading">Book an Outpatient Consultation</h2>
            <p className="card-description">
              Select department, specialist, and available time window. A clinical OPD token will be generated upon confirmation.
            </p>
          </div>

          <form onSubmit={handleSubmitBooking} className="consultation-form">
            {/* Step 1: Specialty & Doctor */}
            <fieldset className="form-fieldset">
              <legend>1. Clinical Specialty & Specialist</legend>
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="dept-select">
                    Department <span className="req">*</span>
                  </label>
                  <select
                    id="dept-select"
                    required
                    value={form.department}
                    onChange={(e) => updateFormField('department', e.target.value)}
                  >
                    <option value="">-- Select Clinical Department --</option>
                    {departments.map((dept) => (
                      <option key={dept} value={dept}>
                        {dept}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="doctor-select">
                    Consulting Specialist <span className="req">*</span>
                  </label>
                  <select
                    id="doctor-select"
                    required
                    disabled={!form.department}
                    value={form.doctorId}
                    onChange={(e) => updateFormField('doctorId', e.target.value)}
                  >
                    <option value="">
                      {form.department
                        ? '-- Choose Specialist --'
                        : '-- First select a department --'}
                    </option>
                    {filteredDoctors.map((doc) => (
                      <option key={doc.id} value={doc.id}>
                        {doc.name} — {doc.title} ({doc.experience_years} yrs exp)
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </fieldset>

            {/* Step 2: Date & Slot */}
            <fieldset className="form-fieldset">
              <legend>2. Consultation Slot</legend>
              <div className="form-group">
                <label htmlFor="slot-select">
                  Available Outpatient Slot <span className="req">*</span>
                </label>
                <select
                  id="slot-select"
                  required
                  disabled={!form.doctorId}
                  value={form.slotId}
                  onChange={(e) => updateFormField('slotId', e.target.value)}
                >
                  <option value="">
                    {form.doctorId
                      ? availableSlots.length > 0
                        ? '-- Select Consultation Slot --'
                        : 'No available slots for this specialist'
                      : '-- First select a specialist --'}
                  </option>
                  {availableSlots.map((slot) => (
                    <option key={slot.id} value={slot.id}>
                      {slot.date} | {slot.start_time} - {slot.end_time} (OPD Consultation)
                    </option>
                  ))}
                </select>
                {selectedDoctor && availableSlots.length === 0 && (
                  <p className="field-hint-warning">
                    All slots for {selectedDoctor.name} are currently occupied. Please choose another doctor or check with the front desk.
                  </p>
                )}
              </div>
            </fieldset>

            {/* Step 3: Patient Information */}
            <fieldset className="form-fieldset">
              <legend>3. Patient Demographics & Contact</legend>
              <div className="form-row form-row-3">
                <div className="form-group">
                  <label htmlFor="patient-name">
                    Full Name <span className="req">*</span>
                  </label>
                  <input
                    id="patient-name"
                    required
                    minLength={2}
                    maxLength={80}
                    placeholder="Patient's official name"
                    value={form.patientName}
                    onChange={(e) => updateFormField('patientName', e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="patient-age">
                    Age (Years) <span className="req">*</span>
                  </label>
                  <input
                    id="patient-age"
                    required
                    type="number"
                    min="1"
                    max="120"
                    placeholder="e.g. 35"
                    value={form.patientAge}
                    onChange={(e) => updateFormField('patientAge', e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="patient-gender">
                    Gender <span className="req">*</span>
                  </label>
                  <select
                    id="patient-gender"
                    required
                    value={form.patientGender}
                    onChange={(e) => updateFormField('patientGender', e.target.value)}
                  >
                    <option value="Male">Male</option>
                    <option value="Female">Female</option>
                    <option value="Other">Other</option>
                    <option value="Prefer not to say">Prefer not to say</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label htmlFor="patient-phone">
                  Mobile Number (10 Digits) <span className="req">*</span>
                </label>
                <input
                  id="patient-phone"
                  required
                  type="tel"
                  placeholder="e.g. 9876543210"
                  pattern="[0-9]{10,14}"
                  value={form.phone}
                  onChange={(e) => updateFormField('phone', e.target.value)}
                />
                <small className="field-hint">Token confirmation will be associated with this contact number.</small>
              </div>

              <div className="form-group">
                <label htmlFor="patient-reason">
                  Primary Clinical Concern / Reason for Visit <span className="req">*</span>
                </label>
                <textarea
                  id="patient-reason"
                  required
                  rows={3}
                  minLength={4}
                  maxLength={500}
                  placeholder="State primary symptoms or follow-up reason (e.g. chronic hypertension review, persistent cough, knee stiffness)"
                  value={form.reason}
                  onChange={(e) => updateFormField('reason', e.target.value)}
                />
              </div>
            </fieldset>

            {/* Step 4: Medical Triage Acknowledgment */}
            <div className="form-ack">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  required
                  checked={form.ackNonEmergency}
                  onChange={(e) => updateFormField('ackNonEmergency', e.target.checked)}
                />
                <span>
                  I confirm that the patient is stable and does not require immediate acute resuscitation, trauma, or emergency casualty care.
                </span>
              </label>
            </div>

            <div className="form-actions">
              <button
                type="submit"
                className="btn-primary btn-large"
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Verifying & Generating Token...' : 'Confirm Appointment & Issue Token'}
              </button>
            </div>
          </form>
        </section>

        {/* RIGHT COLUMN: LIVE CONSULTATION SUMMARY & CLINICAL INSTRUCTIONS */}
        <aside className="summary-sidebar" aria-label="Appointment summary review">
          <div className="summary-card">
            <div className="summary-card-header">
              <span className="section-eyebrow">PREVIEW</span>
              <h3>Consultation Details</h3>
            </div>

            <div className="summary-list">
              <div className="summary-item">
                <span className="item-label">Department</span>
                <span className="item-value">{form.department || 'Not selected'}</span>
              </div>

              <div className="summary-item">
                <span className="item-label">Specialist</span>
                <span className="item-value">
                  {selectedDoctor ? selectedDoctor.name : 'Not selected'}
                </span>
                {selectedDoctor && (
                  <span className="item-sub">{selectedDoctor.title}</span>
                )}
              </div>

              <div className="summary-item">
                <span className="item-label">Clinic Location</span>
                <span className="item-value">
                  {selectedDoctor ? selectedDoctor.location : 'Will be specified on selection'}
                </span>
              </div>

              <div className="summary-item">
                <span className="item-label">Scheduled Slot</span>
                <span className="item-value">
                  {selectedSlot
                    ? `${selectedSlot.date} at ${selectedSlot.start_time} - ${selectedSlot.end_time}`
                    : 'Not selected'}
                </span>
              </div>

              <div className="summary-item">
                <span className="item-label">Consultation Type</span>
                <span className="item-value">In-Person Outpatient (OPD)</span>
              </div>

              <div className="summary-item">
                <span className="item-label">Standard OPD Fee</span>
                <span className="item-value">Payable at OPD Counter / Free Registration</span>
              </div>
            </div>

            <div className="clinical-guidance">
              <h4>Patient Checklist</h4>
              <ul>
                <li>Government-issued Identity Proof (Aadhaar / Voter ID / Passport)</li>
                <li>Previous medical summary or active drug prescriptions</li>
                <li>Recent diagnostic or lab investigation reports</li>
                <li>Arrive 15 minutes before the allocated slot</li>
              </ul>
            </div>
          </div>
        </aside>
      </main>

      {/* 7. MEDICAL FACULTY DIRECTORY */}
      <section className="portal-container section-container" id="specialists-directory">
        <div className="section-header-block">
          <span className="section-eyebrow">CLINICAL FACULTY</span>
          <h2>Consulting Doctors & Outpatient Schedules</h2>
          <p>
            Board-certified consultants available across General Medicine, Cardiology, Dermatology, Orthopedics, and Pediatrics.
          </p>
        </div>

        <div className="doctor-cards-grid">
          {doctors.map((doc) => {
            const docSlots = slots.filter(
              (s) => s.doctor_id === doc.id && s.status === 'available',
            )

            return (
              <article key={doc.id} className="doctor-profile-card">
                <div className="doctor-card-top">
                  <div className="doctor-avatar" aria-hidden="true">
                    {doc.name.replace('Dr. ', '').slice(0, 2).toUpperCase()}
                  </div>
                  <div>
                    <h3 className="doctor-card-name">{doc.name}</h3>
                    <p className="doctor-card-title">{doc.title}</p>
                    <span className="dept-tag">{doc.department}</span>
                  </div>
                </div>

                <div className="doctor-card-meta">
                  <div className="meta-row">
                    <span className="meta-label">Clinical Experience:</span>
                    <span className="meta-data">{doc.experience_years} Years</span>
                  </div>
                  <div className="meta-row">
                    <span className="meta-label">OPD Wing / Room:</span>
                    <span className="meta-data">{doc.location}</span>
                  </div>
                  <div className="meta-row">
                    <span className="meta-label">Open Slots:</span>
                    <span className={`slot-count-badge ${docSlots.length > 0 ? 'active' : 'exhausted'}`}>
                      {docSlots.length} slot{docSlots.length === 1 ? '' : 's'} available
                    </span>
                  </div>
                </div>

                <button
                  type="button"
                  className="btn-select-doctor"
                  onClick={() => handleSelectDoctor(doc)}
                >
                  Book with {doc.name.split(' ')[1] || doc.name} →
                </button>
              </article>
            )
          })}
        </div>
      </section>

      {/* 8. HOSPITAL OPD FRONT DESK / ADMIN RECORDS TABLE */}
      {/* 8. PATIENT'S OWN APPOINTMENT RECORDS */}
      <MyAppointments onChanged={loadData} reloadKey={recordsKey} />

      {/* 9. PATIENT GUIDELINES & HOSPITAL FAQ */}
      <section className="portal-container section-container" id="patient-guidelines">
        <div className="section-header-block">
          <span className="section-eyebrow">CONSULTATION STANDARDS</span>
          <h2>Patient Rights & Outpatient Regulations</h2>
        </div>
        <div className="guidelines-grid">
          <div className="guideline-card">
            <h4>Check-in & Verification</h4>
            <p>
              Please present your government photo ID and your generated Token ID at the reception desk counter. OPD consultations are sequenced by allocated appointment windows.
            </p>
          </div>
          <div className="guideline-card">
            <h4>Cancellation & Rescheduling</h4>
            <p>
              If you cannot attend, please cancel your booking via this portal or phone the desk at least 2 hours before your scheduled time to release the slot for patients on standby.
            </p>
          </div>
          <div className="guideline-card">
            <h4>Billing & Prescription Policy</h4>
            <p>
              Prescriptions are archived digitally in our electronic health record system. Standard clinical consultation receipts are issued upon in-person registration.
            </p>
          </div>
        </div>
      </section>

      {/* 10. OFFICIAL HOSPITAL FOOTER */}
      <footer className="hospital-footer">
        <div className="portal-container footer-inner">
          <div className="footer-col brand-col">
            <div className="hospital-brand footer-brand">
              <div className="hospital-seal" aria-hidden="true">+</div>
              <div>
                <strong>MediVoc Memorial Hospital</strong>
                <p>NABH Accredited Tertiary Care Center</p>
              </div>
            </div>
            <p className="footer-address">
              Plot 42, Health City Boulevard, Central Medical Enclave,
              <br />
              Bangalore, Karnataka 560029, India
            </p>
          </div>

          <div className="footer-col">
            <h4>24/7 Helplines</h4>
            <ul>
              <li>Emergency & Trauma: 108 / 1066</li>
              <li>Ambulance Dispatch: (080) 4000-1199</li>
              <li>Blood Bank & Transfusion: Ext. 305</li>
              <li>OPD Central Desk: (080) 4000-1000</li>
            </ul>
          </div>

          <div className="footer-col">
            <h4>Clinical Departments</h4>
            <ul>
              <li>General Internal Medicine</li>
              <li>Cardiology & Cardiac Catheterization</li>
              <li>Dermatology, Venereology & Cosmetology</li>
              <li>Orthopedic Surgery & Joint Replacement</li>
              <li>Pediatrics & Neonatal Intensive Care</li>
            </ul>
          </div>

          <div className="footer-col">
            <h4>Governance & Compliance</h4>
            <ul>
              <li>Medical Council Accreditation No: MC-2024-9981</li>
              <li>Patient Privacy & Health Records Policy</li>
              <li>NABH Certified Quality Standards</li>
              <li>Hospital Infection Control Committee (HICC)</li>
            </ul>
          </div>
        </div>

        <div className="portal-container footer-bottom">
          <p>© 2026 MediVoc Memorial Hospital & Medical Research Institute. All rights reserved.</p>
          <p>
            Emergency Notice: This website does not provide diagnostic or medical opinions. In life-threatening emergencies, visit our Casualty department or call 108.
          </p>
        </div>
      </footer>

      {/* 11. VOICE AI AGENT (GEMINI + LANGGRAPH) */}
      <VoiceAssistant apiUrl={API_URL} onAppointmentBooked={handleVoiceAppointmentBooked} />
    </div>
  )
}
