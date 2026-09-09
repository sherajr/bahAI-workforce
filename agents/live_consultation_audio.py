"""
Recording the room, and working out who said what afterwards (rule 91).

Added 2026-08-25 by owner decision. Until this existed, `record_audio` was
REFUSED outright and the setup screen told the room nothing was being kept —
that was honest then, because no recorder existed. It is now a real, explicit,
per-meeting choice, and the honesty requirement moved rather than went away: the
setup screen has to say plainly that the room is being recorded, and the people
in the room have to be told. A recording that happens quietly is the one thing
this must never be.

**Why record at all.** Sheraj asked for named speakers in the transcript. The
live session cannot do it: OpenAI's diarisation model
(`gpt-4o-transcribe-diarize`) is documented as NOT available to the Realtime
API — checked 2026-08-25, its model page lists realtime transcription as "Not
supported" — so nothing during the meeting can tell one voice from another. It
works on a finished audio file. So: record, then diarise afterwards.

**Diarisation is not recognition, and this module never crosses that line.**
The model returns "A", "B", "C" — voices it can tell apart, not people it knows.
A human then says which name belongs to which letter, once, and
`store.apply_speaker_names` writes it through. The API can accept voice
REFERENCE clips to skip that step; this module does not use them and must not
start. That is biometric enrolment of Sheraj's friends, the original spec ruled
it out, and one manual mapping per meeting is a small price for not holding
voiceprints of people who came round for a consultation.

Everything here is private (rule 73): audio under `private/consultation_audio/`,
git-ignored, deleted with the session. Nothing about it reaches `workforce.db`
except the money.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import requests

from agents.live_consultation import DIARIZE_MODEL, DICTATE_MODEL

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TRANSCRIPTIONS_URL = f"{OPENAI_BASE}/audio/transcriptions"

# OpenAI's upload ceiling for this endpoint. A meeting recorded as Opus at the
# browser's default runs roughly 0.5 MB a minute, so this is hours — but a
# refusal has to be a sentence a person can act on, not a 413 from a library.
MAX_UPLOAD_BYTES = int(os.getenv("CONSULTATION_AUDIO_MAX_BYTES", str(25 * 1024 * 1024)))

# Priced per minute of audio. Metered like everything else that costs money.
COST_PER_MINUTE = float(os.getenv("CONSULTATION_DIARIZE_COST_PER_MIN", "0.006"))
SPEND_KIND = "openai_transcribe"

ACCEPTED_SUFFIXES = (".webm", ".ogg", ".oga", ".mp3", ".m4a", ".mp4", ".wav", ".flac")

# Dictation is a few seconds of one person talking into a text box. The cap is
# small on purpose: anything approaching this is not dictation, and a runaway
# recorder should be refused cheaply rather than uploaded.
DICTATE_MAX_BYTES = int(os.getenv("CONSULTATION_DICTATE_MAX_BYTES", str(8 * 1024 * 1024)))


class AudioError(RuntimeError):
    """Raised with a sentence a non-technical owner can act on."""


def _key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def available() -> bool:
    return bool(_key())


def session_dir(session_id: str) -> Path:
    from agents.live_consultation_store import AUDIO_DIR
    return Path(AUDIO_DIR) / session_id


def recording_path(session_id: str) -> Optional[Path]:
    folder = session_dir(session_id)
    if not folder.exists():
        return None
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in ACCEPTED_SUFFIXES:
            return f
    return None


def save_recording(session_id: str, data: bytes, filename: str = "meeting.webm") -> Path:
    """Write the room's recording into the private folder, replacing any earlier one."""
    if not data:
        raise AudioError("The recording arrived empty; nothing was saved.")
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES / (1024 * 1024)
        raise AudioError(
            f"That recording is larger than the {mb:.0f} MB OpenAI accepts in one piece, "
            "so speaker names cannot be worked out from it. The meeting itself, the "
            "transcript and the report are all unaffected.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        suffix = ".webm"
    folder = session_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    path = folder / f"meeting{suffix}"
    path.write_bytes(data)
    return path


def estimate_cost(seconds: float) -> float:
    return round(max(0.0, seconds) / 60.0 * COST_PER_MINUTE, 4)


def _meter(seconds: float) -> Optional[float]:
    cost = estimate_cost(seconds)
    try:
        from agents.state import record_spend
        record_spend(SPEND_KIND, cost)
    except Exception:
        return None
    return cost


def _segments_from(payload: dict) -> list[dict]:
    """
    Normalise whatever shape `diarized_json` came back in.

    Written defensively on purpose: this is a comparatively new response format,
    the field names differ between OpenAI's and Azure's implementations, and the
    failure this guards against is a meeting's worth of transcript silently
    coming back as zero segments.
    """
    raw = payload.get("segments")
    if not isinstance(raw, list):
        raw = payload.get("diarized_segments") if isinstance(
            payload.get("diarized_segments"), list) else []
    out: list[dict] = []
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        if speaker is None:
            speaker = seg.get("speaker_id") or seg.get("speaker_label")
        out.append({
            "speaker": str(speaker).strip() if speaker is not None else "",
            "text": text,
            "start": seg.get("start"),
            "end": seg.get("end"),
        })
    return out


def transcribe_diarized(path: Path | str, model: str = "", post=None) -> dict:
    """
    Send the recording for a speaker-separated transcript.

    `post` is injectable so the suite can prove the request shape and every
    parsing branch without spending anything — no test may pay to check a schema.

    Returns {segments, speakers, duration, cost}. Speakers are the model's own
    labels ("A", "B"); this function never claims to know who anyone is.
    """
    if not available():
        raise AudioError(
            "Working out who said what needs an OpenAI API key. Add OPENAI_API_KEY to "
            ".env and restart the API. The recording is safe where it is.")
    path = Path(path)
    if not path.exists():
        raise AudioError("The recording for this meeting is no longer on disk.")
    sender = post or requests.post
    try:
        with path.open("rb") as fh:
            resp = sender(
                TRANSCRIPTIONS_URL,
                headers={"Authorization": f"Bearer {_key()}"},
                files={"file": (path.name, fh, "application/octet-stream")},
                data={"model": model or DIARIZE_MODEL, "response_format": "diarized_json"},
                timeout=600,
            )
    except AudioError:
        raise
    except Exception as e:
        raise AudioError(
            f"Could not reach OpenAI to work out the speakers ({type(e).__name__}). "
            "The recording is kept, so this can be tried again.") from e
    if getattr(resp, "status_code", 500) >= 400:
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except Exception:
            detail = (getattr(resp, "text", "") or "")[:300]
        raise AudioError(f"OpenAI refused the recording: {detail}")
    try:
        payload = resp.json()
    except Exception as e:
        raise AudioError("OpenAI's reply could not be read as a transcript.") from e

    segments = _segments_from(payload)
    if not segments:
        raise AudioError(
            "The recording came back with no speech in it. Nothing has been changed; the "
            "live transcript is still the record.")
    duration = payload.get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = max((float(s.get("end") or 0) for s in segments), default=0.0)
    speakers = sorted({s["speaker"] for s in segments if s["speaker"]})
    return {
        "segments": segments,
        "speakers": speakers,
        "duration": duration,
        "cost": _meter(duration),
    }


def transcribe_plain(data: bytes, filename: str = "note.webm", model: str = "",
                     post=None) -> dict:
    """
    Turn a few seconds of speech into text, for typing into a box by voice.

    **Nothing is written to disk, deliberately.** A meeting recording is a record
    and lives under `private/consultation_audio/`; this is a passing utterance on
    its way to becoming a sentence in a form. It exists in memory for one request
    and is gone -- there is no file to delete afterwards and nothing to leak.

    No diarisation and no speaker anything: one person is talking into a text
    box, and rule 91's line about never inferring who is speaking is not even in
    play here.
    """
    if not data:
        raise AudioError("Nothing was recorded. Hold the button while you speak.")
    if len(data) > DICTATE_MAX_BYTES:
        raise AudioError(
            "That is far too long to be dictation. Type it, or record it in shorter pieces.")
    if not available():
        raise AudioError(
            "Dictation needs an OpenAI API key. Add OPENAI_API_KEY to .env and restart "
            "the API, or type it in instead.")
    sender = post or requests.post
    try:
        resp = sender(
            TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {_key()}"},
            files={"file": (filename or "note.webm", data, "application/octet-stream")},
            data={"model": model or DICTATE_MODEL, "response_format": "json"},
            timeout=120,
        )
    except Exception as e:
        raise AudioError(
            f"Could not reach OpenAI to transcribe that ({type(e).__name__}). "
            "Type it in instead.") from e
    if getattr(resp, "status_code", 500) >= 400:
        detail = ""
        try:
            detail = (resp.json().get("error") or {}).get("message", "")
        except Exception:
            detail = (getattr(resp, "text", "") or "")[:300]
        raise AudioError(f"OpenAI could not transcribe that: {detail}")
    try:
        payload = resp.json()
    except Exception as e:
        raise AudioError("OpenAI's reply could not be read as text.") from e
    text = str(payload.get("text") or "").strip()
    if not text:
        raise AudioError("Nothing could be made out. Try again, a little closer to the mic.")
    # Metered like every other paid call. Length is estimated from the payload
    # since a plain transcription reply carries no duration -- a rough figure in
    # the ledger beats a gap for something this small and this frequent.
    seconds = payload.get("duration")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = len(data) / 6000.0
    return {"text": text, "cost": _meter(seconds)}
