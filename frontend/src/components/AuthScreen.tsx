import { useState } from 'react'
import { api, saveSession, type User } from '../api'
import './Panels.css'

type Props = {
  onSignedIn: (user: User) => void
}

const DEMO_ACCOUNTS = [
  { role: 'Administrator', email: 'admin@medivoc.com', password: 'admin123' },
  { role: 'Doctor (Cardiology)', email: 'raghav@medivoc.com', password: 'doctor123' },
  { role: 'Patient', email: 'patient@medivoc.com', password: 'patient123' },
]

export default function AuthScreen({ onSignedIn }: Props) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const path = mode === 'login' ? '/auth/login' : '/auth/register'
      const body =
        mode === 'login' ? { email, password } : { name, email, phone, password }
      const result = await api<{ token: string; user: User }>(path, { method: 'POST', body })
      saveSession(result.token, result.user)
      onSignedIn(result.user)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed.')
    } finally {
      setBusy(false)
    }
  }

  function fillDemo(demoEmail: string, demoPassword: string) {
    setMode('login')
    setEmail(demoEmail)
    setPassword(demoPassword)
    setError('')
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="hospital-seal" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M12 2v20" />
              <path d="M2 12h20" />
            </svg>
          </div>
          <div>
            <h1>MediVoc Memorial Hospital</h1>
            <p>Outpatient Appointment Portal</p>
          </div>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            className={mode === 'login' ? 'active' : ''}
            onClick={() => setMode('login')}
          >
            Sign In
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            className={mode === 'register' ? 'active' : ''}
            onClick={() => setMode('register')}
          >
            Register as Patient
          </button>
        </div>

        <form onSubmit={submit} className="auth-form">
          {mode === 'register' && (
            <>
              <label htmlFor="auth-name">Full Name</label>
              <input
                id="auth-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                minLength={2}
                placeholder="e.g. Rahul Verma"
              />

              <label htmlFor="auth-phone">Mobile Number</label>
              <input
                id="auth-phone"
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                required
                pattern="[0-9+\s-]{10,16}"
                placeholder="10 digit mobile number"
              />
            </>
          )}

          <label htmlFor="auth-email">Email Address</label>
          <input
            id="auth-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
            placeholder="you@example.com"
          />

          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            placeholder={mode === 'register' ? 'At least 6 characters' : ''}
          />

          {error && <p className="auth-error" role="alert">{error}</p>}

          <button type="submit" className="btn-primary-wide" disabled={busy}>
            {busy ? 'Please wait...' : mode === 'login' ? 'Sign In' : 'Create Patient Account'}
          </button>
        </form>

        <div className="auth-demo">
          <strong>Demo logins</strong>
          {DEMO_ACCOUNTS.map((account) => (
            <button
              type="button"
              key={account.email}
              className="demo-chip"
              onClick={() => fillDemo(account.email, account.password)}
            >
              {account.role}
            </button>
          ))}
          <small>Appointment emails are written to backend/data/sent_emails.log unless SMTP is configured.</small>
        </div>
      </div>
    </div>
  )
}
