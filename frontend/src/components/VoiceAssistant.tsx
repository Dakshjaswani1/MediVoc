import React, { useEffect, useRef, useState } from 'react'
import { authHeader } from '../api'
import './VoiceAssistant.css'

interface AppointmentRecord {
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

interface Message {
  id: string
  sender: 'agent' | 'user'
  text: string
  appointment?: AppointmentRecord
  timestamp: string
}

interface VoiceAssistantProps {
  apiUrl: string
  /** Which desk this widget is: the backend picks the agent from the signed-in role. */
  role?: 'patient' | 'doctor'
  /** Patient desk only: a booking the agent just made, for the token slip. */
  onAppointmentBooked?: (appointment: AppointmentRecord) => void
  /** Called after every agent turn so the surrounding panel can reload. */
  onChanged?: () => void
}

/** Wording per desk. The tools themselves live on the backend, keyed by the same role. */
const DESK_COPY = {
  patient: {
    title: 'MediVoc Voice AI Desk',
    fab: 'Talk to Voice AI',
    placeholder: "Or type here (e.g. 'I need a cardiologist')...",
    greeting:
      'Hello! I am MediVoc Hospital’s AI Voice Receptionist. I can help you find a doctor, check available slots, and book your outpatient appointment. What symptoms or medical department do you need today?',
  },
  doctor: {
    title: 'MediVoc Doctor Voice Desk',
    fab: 'Talk to Desk Assistant',
    placeholder: "Or type here (e.g. 'what does tomorrow look like?')...",
    greeting:
      'Good day, doctor. I can read out your clinic, cancel a consultation you cannot attend, or mark a day of leave. What would you like to do?',
  },
} as const

/**
 * Languages the voice desk speaks. `code` goes to the backend (it picks the Whisper
 * language and the Edge-TTS neural voice); `speech` is the BCP-47 tag for the
 * browser's own SpeechRecognition.
 */
const LANGUAGES = [
  { code: 'en', label: 'English', speech: 'en-US' },
  { code: 'hi', label: 'हिन्दी', speech: 'hi-IN' },
  { code: 'ta', label: 'தமிழ்', speech: 'ta-IN' },
  { code: 'ml', label: 'മലയാളം', speech: 'ml-IN' },
  { code: 'te', label: 'తెలుగు', speech: 'te-IN' },
  { code: 'kn', label: 'ಕನ್ನಡ', speech: 'kn-IN' },
]

// Check for Web Speech API support
const getSpeechRecognitionClass = () => {
  if (typeof window === 'undefined') return null
  const win = window as unknown as {
    SpeechRecognition?: any
    webkitSpeechRecognition?: any
  }
  return win.SpeechRecognition || win.webkitSpeechRecognition || null
}

export default function VoiceAssistant({
  apiUrl,
  role = 'patient',
  onAppointmentBooked,
  onChanged,
}: VoiceAssistantProps) {
  const copy = DESK_COPY[role]
  const [isOpen, setIsOpen] = useState(false)
  const [sessionId] = useState(() => `mv-session-${Date.now()}`)
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [isPlayingAudio, setIsPlayingAudio] = useState(false)
  const [soundEnabled, setSoundEnabled] = useState(true)
  const [textInput, setTextInput] = useState('')
  const [statusText, setStatusText] = useState('Voice Desk Online')
  const [liveTranscript, setLiveTranscript] = useState('')
  const [audioLevel, setAudioLevel] = useState(0) // 0 to 100
  const [micWarning, setMicWarning] = useState('')
  const [language, setLanguage] = useState('en')

  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      sender: 'agent',
      text: DESK_COPY[role].greeting,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ])

  // Refs for audio handling
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const recognitionRef = useRef<any>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animFrameRef = useRef<number | null>(null)
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const capturedTextRef = useRef<string>('')
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Clean up audio & recognition on unmount
  useEffect(() => {
    return () => {
      cleanupRecording()
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
      }
    }
  }, [])

  function playAudioResponse(audioDataUri: string) {
    if (!soundEnabled || !audioDataUri) return

    try {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
      }
      const audio = new Audio(audioDataUri)
      currentAudioRef.current = audio
      setIsPlayingAudio(true)

      audio.onended = () => {
        setIsPlayingAudio(false)
      }
      audio.onerror = () => {
        setIsPlayingAudio(false)
      }

      audio.play().catch((err) => {
        console.warn('Audio auto-play prevented:', err)
        setIsPlayingAudio(false)
      })
    } catch (err) {
      console.error('Error playing audio:', err)
      setIsPlayingAudio(false)
    }
  }

  // Visualizer loop to sample mic volume
  function startAudioVisualizer(stream: MediaStream) {
    try {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      const ctx = new AudioCtx()
      audioCtxRef.current = ctx
      const source = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 64
      analyser.smoothingTimeConstant = 0.6
      source.connect(analyser)
      analyserRef.current = analyser

      const dataArray = new Uint8Array(analyser.frequencyBinCount)

      let consecutiveQuietTicks = 0

      const checkVolume = () => {
        analyser.getByteFrequencyData(dataArray)
        let sum = 0
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i]
        }
        const avg = sum / dataArray.length
        const level = Math.min(100, Math.round((avg / 128) * 100))
        setAudioLevel(level)

        if (level < 4) {
          consecutiveQuietTicks++
          if (consecutiveQuietTicks > 60) {
            // ~2 seconds of complete silence
            setMicWarning('Microphone input is very quiet. Check your Windows mic volume / mute switch.')
          }
        } else {
          consecutiveQuietTicks = 0
          setMicWarning('')
        }

        animFrameRef.current = requestAnimationFrame(checkVolume)
      }

      checkVolume()
    } catch (err) {
      console.warn('AudioContext visualizer not supported:', err)
    }
  }

  function cleanupRecording() {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = null
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {})
      audioCtxRef.current = null
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    if (recognitionRef.current) {
      try {
        recognitionRef.current.onresult = null
        recognitionRef.current.onerror = null
        recognitionRef.current.onend = null
        recognitionRef.current.stop()
      } catch {
        // Ignore stop error
      }
      recognitionRef.current = null
    }
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = null
    }
    setAudioLevel(0)
    setMicWarning('')
  }

  async function startRecording() {
    try {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
        setIsPlayingAudio(false)
      }

      cleanupRecording()
      setLiveTranscript('')
      capturedTextRef.current = ''
      audioChunksRef.current = []

      // Request microphone access
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      // Start live volume visualizer
      startAudioVisualizer(stream)

      const SpeechClass = getSpeechRecognitionClass()
      let recognitionStarted = false

      if (SpeechClass) {
        try {
          const recognition = new SpeechClass()
          recognition.continuous = true
          recognition.interimResults = true
          recognition.lang = LANGUAGES.find((item) => item.code === language)?.speech ?? 'en-US'

          recognition.onresult = (event: any) => {
            let interim = ''
            let final = ''

            for (let i = event.resultIndex; i < event.results.length; i++) {
              const res = event.results[i]
              const transcript = res[0]?.transcript || ''
              if (res.isFinal) {
                final += transcript + ' '
              } else {
                interim += transcript
              }
            }

            const current = (final || interim).trim()
            if (current) {
              setLiveTranscript(current)
              capturedTextRef.current = current
            }
          }

          recognition.onerror = (event: any) => {
            console.warn('[SpeechRecognition error]', event.error)
            // If SpeechRecognition encounters error, fallback is already collecting audio chunks via MediaRecorder!
          }

          recognition.start()
          recognitionRef.current = recognition
          recognitionStarted = true
        } catch (err) {
          console.warn('[SpeechRecognition init failed, falling back to MediaRecorder]', err)
        }
      }

      // Concurrently run MediaRecorder as audio backup (for Whisper STT backend)
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : MediaRecorder.isTypeSupported('audio/mp4')
            ? 'audio/mp4'
            : ''

      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      mediaRecorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          audioChunksRef.current.push(e.data)
        }
      }

      // Time slice of 250ms ensures audio chunks are continuously buffered!
      recorder.start(250)

      setIsRecording(true)
      setStatusText(
        recognitionStarted
          ? 'Listening... Speak your symptoms or specialist request'
          : 'Recording audio... Speak now',
      )
    } catch (err: unknown) {
      console.error('Microphone access failed:', err)
      const isNotAllowed = err instanceof Error && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')
      setStatusText(
        isNotAllowed
          ? 'Microphone blocked by browser. Please allow microphone access in your address bar icon.'
          : 'Microphone not detected. Please verify your microphone is plugged in.',
      )
    }
  }

  async function stopRecording() {
    if (!isRecording) return

    setIsRecording(false)
    setStatusText('Processing your request...')

    // If live text recognition captured spoken words, dispatch directly to /voice/chat!
    const capturedText = (capturedTextRef.current || liveTranscript).trim()

    // Stop MediaRecorder and grab audio blob if available
    let recordedBlob: Blob | null = null
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop()
      } catch {
        // Ignore
      }
    }

    if (audioChunksRef.current.length > 0) {
      const mime = mediaRecorderRef.current?.mimeType || 'audio/webm'
      recordedBlob = new Blob(audioChunksRef.current, { type: mime })
    }

    cleanupRecording()

    if (capturedText) {
      // Primary Route: Fast text through LangGraph ReAct agent + Edge-TTS speech
      await sendUtteranceToBackend(capturedText)
    } else if (recordedBlob && recordedBlob.size > 800) {
      // Secondary Route: Send raw audio to Whisper STT backend (/voice/interact)
      await sendAudioToBackend(recordedBlob)
    } else {
      setStatusText('No speech detected. Please click the mic and speak clearly.')
      setTimeout(() => setStatusText('Voice Desk Online'), 3500)
    }

    setLiveTranscript('')
  }

  /** Surfaces the backend's own detail instead of a bare status code. */
  async function describeFailure(response: Response): Promise<string> {
    try {
      const body = await response.json()
      const detail = body?.detail
      if (Array.isArray(detail)) return detail.map((item: any) => item.msg).join(', ')
      if (detail) return String(detail)
    } catch {
      // no JSON body, fall through to the status code
    }
    return `Server returned status ${response.status}`
  }

  function blobToBase64(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onloadend = () => {
        const base64 = (reader.result as string).split(',')[1]
        resolve(base64)
      }
      reader.onerror = reject
      reader.readAsDataURL(blob)
    })
  }

  // Route A: Send recognized text directly to LangGraph ReAct Agent
  async function sendUtteranceToBackend(text: string) {
    if (!text || isProcessing) return

    const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    setMessages((prev) => [
      ...prev,
      {
        id: `usr-${Date.now()}`,
        sender: 'user',
        text,
        timestamp: timeStr,
      },
    ])

    setIsProcessing(true)
    setStatusText('Thinking with Gemini 2.5 Flash...')

    try {
      const response = await fetch(`${apiUrl}/voice/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          generate_audio: soundEnabled,
          language,
        }),
      })

      if (!response.ok) {
        throw new Error(await describeFailure(response))
      }

      const data = await response.json()
      setMessages((prev) => [
        ...prev,
        {
          id: `agt-${Date.now()}`,
          sender: 'agent',
          text: data.reply,
          appointment: data.appointment,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ])

      if (data.audio) {
        playAudioResponse(data.audio)
      }

      if (data.appointment) {
        onAppointmentBooked?.(data.appointment)
      }

      // A doctor's turn can cancel a consultation or mark leave, so the panel reloads.
      onChanged?.()

      setStatusText('Voice Desk Online')
    } catch (err) {
      console.error('Utterance dispatch error:', err)
      setStatusText(err instanceof Error ? err.message : 'Error communicating with Voice Agent.')
    } finally {
      setIsProcessing(false)
    }
  }

  // Route B: Send raw recorded audio to local Whisper STT (/voice/interact)
  async function sendAudioToBackend(audioBlob: Blob) {
    setIsProcessing(true)
    setStatusText('Transcribing speech with local Whisper...')

    try {
      const base64Audio = await blobToBase64(audioBlob)
      const ext = audioBlob.type.includes('mp4') ? 'mp4' : 'webm'

      const response = await fetch(`${apiUrl}/voice/interact`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeader() },
        body: JSON.stringify({
          session_id: sessionId,
          audio_base64: base64Audio,
          format: ext,
          language,
        }),
      })

      if (!response.ok) {
        throw new Error(await describeFailure(response))
      }

      const data = await response.json()
      const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

      if (data.user_text) {
        setMessages((prev) => [
          ...prev,
          {
            id: `usr-${Date.now()}`,
            sender: 'user',
            text: data.user_text,
            timestamp: timeStr,
          },
        ])
      }

      if (data.reply) {
        setMessages((prev) => [
          ...prev,
          {
            id: `agt-${Date.now()}`,
            sender: 'agent',
            text: data.reply,
            appointment: data.appointment,
            timestamp: timeStr,
          },
        ])
      }

      if (data.audio) {
        playAudioResponse(data.audio)
      }

      if (data.appointment) {
        onAppointmentBooked?.(data.appointment)
      }

      // A doctor's turn can cancel a consultation or mark leave, so the panel reloads.
      onChanged?.()

      setStatusText('Voice Desk Online')
    } catch (err) {
      console.error('Voice interaction error:', err)
      setStatusText('Failed to process voice request. Please check backend connection.')
    } finally {
      setIsProcessing(false)
    }
  }

  async function handleSendText(e?: React.FormEvent) {
    if (e) e.preventDefault()
    const text = textInput.trim()
    if (!text || isProcessing) return
    setTextInput('')
    await sendUtteranceToBackend(text)
  }

  return (
    <div className="voice-fab-container">
      {isOpen && (
        <div className="voice-window" role="dialog" aria-label="MediVoc Voice AI Assistant">
          {/* Window Header */}
          <div className="voice-window-header">
            <div className="voice-header-meta">
              <div className="voice-agent-avatar" aria-hidden="true">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" y1="19" x2="12" y2="22" />
                </svg>
              </div>
              <div className="voice-header-titles">
                <h3>{copy.title}</h3>
                <p>
                  <span className="status-dot"></span> Powered by Gemini & LangGraph
                </p>
              </div>
            </div>

            <div className="voice-header-actions">
              <select
                className="voice-lang-select"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                title="Conversation language"
                aria-label="Conversation language"
              >
                {LANGUAGES.map((item) => (
                  <option key={item.code} value={item.code}>
                    {item.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="voice-btn-icon"
                title={soundEnabled ? 'Mute AI Voice' : 'Unmute AI Voice'}
                onClick={() => setSoundEnabled(!soundEnabled)}
              >
                {soundEnabled ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                    <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                    <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                    <line x1="23" y1="9" x2="17" y2="15" />
                    <line x1="17" y1="9" x2="23" y2="15" />
                  </svg>
                )}
              </button>
              <button
                type="button"
                className="voice-btn-icon"
                title="Close Voice Assistant"
                onClick={() => {
                  cleanupRecording()
                  setIsOpen(false)
                }}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </div>

          {/* Conversation Transcript */}
          <div className="voice-messages">
            {messages.map((msg) => (
              <div key={msg.id} className={`voice-bubble ${msg.sender}`}>
                <div>{msg.text}</div>
                {msg.appointment && (
                  <div className="voice-booked-card">
                    <strong>Confirmed Token: {msg.appointment.reference_no ?? `MV-2026-${msg.appointment.id}`}</strong>
                    <small>
                      Doctor: {msg.appointment.doctor_name} | {msg.appointment.date} ({msg.appointment.start_time})
                    </small>
                  </div>
                )}
                <span className="voice-bubble-time">{msg.timestamp}</span>
              </div>
            ))}

            {/* Live speech preview while speaking */}
            {isRecording && liveTranscript && (
              <div className="voice-bubble user live-preview">
                <div className="live-preview-tag">Hearing you:</div>
                <div>{liveTranscript}</div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Status Bar with dynamic level meter */}
          <div className="voice-status-bar">
            <span className="voice-status-label">{statusText}</span>
            {(isRecording || isPlayingAudio || isProcessing) && (
              <div className="wave-bars" title={isRecording ? `Mic input level: ${audioLevel}%` : 'AI Activity'}>
                <span
                  className="wave-bar"
                  style={{ height: isRecording ? `${Math.max(4, (audioLevel * 18) / 100)}px` : undefined }}
                ></span>
                <span
                  className="wave-bar"
                  style={{ height: isRecording ? `${Math.max(6, (audioLevel * 22) / 100)}px` : undefined }}
                ></span>
                <span
                  className="wave-bar"
                  style={{ height: isRecording ? `${Math.max(5, (audioLevel * 20) / 100)}px` : undefined }}
                ></span>
                <span
                  className="wave-bar"
                  style={{ height: isRecording ? `${Math.max(4, (audioLevel * 16) / 100)}px` : undefined }}
                ></span>
              </div>
            )}
          </div>

          {micWarning && <div className="mic-warning-strip">{micWarning}</div>}

          {/* Voice Controls */}
          <div className="voice-controls">
            <div className="voice-mic-center">
              <button
                type="button"
                className={`mic-toggle-btn ${isRecording ? 'recording' : ''}`}
                onClick={isRecording ? stopRecording : startRecording}
                disabled={isProcessing}
                style={{
                  transform: isRecording && audioLevel > 15 ? `scale(${1 + audioLevel / 400})` : undefined,
                }}
                title={isRecording ? 'Click to stop speaking and submit' : 'Click to speak with the Voice Agent'}
              >
                {isRecording ? (
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                    <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                  </svg>
                ) : (
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" y1="19" x2="12" y2="22" />
                  </svg>
                )}
              </button>
            </div>
            <div className="mic-hint">
              {isRecording
                ? 'Listening... Click the red button when finished speaking'
                : 'Click mic to speak, or type your message below'}
            </div>

            {/* Fallback Text Input */}
            <form onSubmit={handleSendText} className="voice-text-form">
              <input
                type="text"
                className="voice-text-input"
                placeholder={copy.placeholder}
                value={textInput}
                onChange={(e) => setTextInput(e.target.value)}
                disabled={isProcessing || isRecording}
              />
              <button
                type="submit"
                className="voice-send-btn"
                disabled={isProcessing || isRecording || !textInput.trim()}
              >
                Send
              </button>
            </form>
          </div>
        </div>
      )}

      {/* Floating Trigger Button */}
      <button
        type="button"
        className={`voice-fab ${isOpen ? 'active' : ''}`}
        onClick={() => {
          if (isOpen) {
            cleanupRecording()
          }
          setIsOpen(!isOpen)
        }}
        aria-label="Toggle Voice AI Assistant"
      >
        <span className="voice-fab-pulse"></span>
        <span className="voice-fab-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="22" />
          </svg>
        </span>
        <span>{isOpen ? 'Close Voice Desk' : copy.fab}</span>
      </button>
    </div>
  )
}
