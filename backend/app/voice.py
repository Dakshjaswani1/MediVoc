import asyncio
import base64
import io
import os
import shutil
import tempfile
from typing import Optional
import edge_tts
import pyttsx3

# Ensure ffmpeg executable is available on Windows
def _configure_ffmpeg_path():
    if shutil.which("ffmpeg"):
        return
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        d = os.path.dirname(exe)
        target = os.path.join(d, "ffmpeg.exe")
        if not os.path.exists(target):
            shutil.copy2(exe, target)
        if d not in os.environ["PATH"]:
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
            print(f"[Voice] Auto-configured ffmpeg from imageio-ffmpeg at: {target}")
    except Exception as e:
        print(f"[Voice] Warning: Could not configure ffmpeg: {e}")

_configure_ffmpeg_path()

# Edge-TTS neural voice per language code. Whisper is given the same code for STT,
# and the browser's SpeechRecognition gets the matching BCP-47 tag from the frontend.
#
# This dict is the single source of truth for which languages the voice desk speaks:
# app.agent.LANGUAGE_NAMES names them for the prompt and LANGUAGE_PATTERN below guards
# the request bodies, so adding a language means adding one line here and one there.
VOICES = {
    "en": "en-US-JennyNeural",
    "hi": "hi-IN-SwaraNeural",
    "ta": "ta-IN-PallaviNeural",
    "ml": "ml-IN-SobhanaNeural",
    "te": "te-IN-ShrutiNeural",
    "kn": "kn-IN-SapnaNeural",
}

# Pydantic pattern for a supported language code, built from VOICES so the API can
# never accept a code that has no voice (the old hardcoded "^(en|hi)$" rejected the
# four Indian languages the frontend dropdown offered).
LANGUAGE_PATTERN = f"^({'|'.join(VOICES)})$"

# Global lazy-loaded Whisper model
_whisper_model = None


def get_whisper_model():
    global _whisper_model
    _configure_ffmpeg_path()
    if _whisper_model is None:
        import whisper
        # Load the lightweight 'tiny' or 'base' model for fast local transcription.
        # ponytail: must stay a multilingual checkpoint. An English-only one ('tiny.en',
        # 'base.en') transcribes Hindi or Tamil speech into English-looking nonsense
        # instead of failing, so do not set WHISPER_MODEL to a '.en' model.
        model_name = os.getenv("WHISPER_MODEL", "tiny")
        _whisper_model = whisper.load_model(model_name)
    return _whisper_model


def transcribe_audio_file(file_path: str, language: Optional[str] = None) -> str:
    """Transcribes an audio file on disk using local Whisper.

    `language` is a code from VOICES ('en', 'hi', 'ta', ...). None lets Whisper
    detect the language itself, which is slower and less reliable on short clips.
    """
    _configure_ffmpeg_path()
    model = get_whisper_model()
    result = model.transcribe(file_path, fp16=False, language=language or None)
    return result.get("text", "").strip()


def transcribe_audio_bytes(
    audio_bytes: bytes, extension: str = ".webm", language: Optional[str] = None
) -> str:
    """Transcribes in-memory audio bytes using local Whisper."""
    with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        text = transcribe_audio_file(tmp_path, language)
        return text
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def synthesize_speech_edge(text: str, voice: str = "en-US-JennyNeural") -> bytes:
    """Synthesizes high-quality neural speech using edge-tts."""
    communicate = edge_tts.Communicate(text, voice)
    audio_buffer = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.extend(chunk["data"])
    return bytes(audio_buffer)


def synthesize_speech_pyttsx3(text: str) -> bytes:
    """Offline fallback speech synthesis using local Windows SAPI / pyttsx3."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def text_to_speech_base64(text: str, language: str = "en") -> str:
    """
    Converts text to speech and returns a data URI (e.g. data:audio/mp3;base64,...).
    Tries edge-tts first; falls back to pyttsx3.

    `language` picks the neural voice. The pyttsx3 fallback is English-only, so a
    non-English reply that falls through will be read with an English voice.
    """
    # Clean text for speech (remove markdown asterisks, brackets, hashes)
    clean_text = (
        text.replace("*", "")
        .replace("#", "")
        .replace("`", "")
        .replace("[", "")
        .replace("]", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )

    if not clean_text:
        return ""

    try:
        # Try Edge-TTS (ultra realistic neural voice)
        audio_bytes = await synthesize_speech_edge(clean_text, VOICES.get(language, VOICES["en"]))
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return f"data:audio/mp3;base64,{b64}"
    except Exception as e:
        print(f"[Voice] Edge TTS failed ({e}), falling back to pyttsx3...")
        try:
            audio_bytes = synthesize_speech_pyttsx3(clean_text)
            b64 = base64.b64encode(audio_bytes).decode("utf-8")
            return f"data:audio/wav;base64,{b64}"
        except Exception as e2:
            print(f"[Voice] pyttsx3 fallback also failed: {e2}")
            return ""
