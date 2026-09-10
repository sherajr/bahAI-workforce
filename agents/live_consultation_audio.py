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
import re
from pathlib import Path
from typing import Optional

import requests

from agents.live_consultation import DIARIZE_MODEL, DICTATE_MODEL

OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
TRANSCRIPTIONS_URL = f"{OPENAI_BASE}/audio/transcriptions"

# OpenAI's upload ceiling for this endpoint, and a real limit on how long a
# meeting this can separate speakers for.
#
# The comment here used to say 0.5 MB a minute made 25 MB "hours". That is
# wrong, and wrong in the direction that wastes somebody's afternoon: at the
# browser's actual default the ceiling is reached in well under an hour, and the
# failure arrived only once the meeting was over and the file was being sent.
# The honest number is computed, warned about EARLY, and stated on screen while
# the recording is still running (rule 108).
MAX_UPLOAD_BYTES = int(os.getenv("CONSULTATION_AUDIO_MAX_BYTES", str(25 * 1024 * 1024)))

# Measured from real MediaRecorder output rather than assumed: Opus in WebM at
# the browser default sits around 0.75 MB/min. Used ONLY to warn in advance --
# the actual refusal is always on measured bytes, never on this estimate.
BYTES_PER_MINUTE_ESTIMATE = float(os.getenv("CONSULTATION_AUDIO_BYTES_PER_MIN",
                                            str(0.75 * 1024 * 1024)))
# Warn while there is still time to do something about it.
WARN_AT_FRACTION = 0.8


def upload_budget() -> dict:
    """What the recording limit actually means, in minutes, for the UI to say."""
    minutes = MAX_UPLOAD_BYTES / BYTES_PER_MINUTE_ESTIMATE
    return {
        "max_bytes": MAX_UPLOAD_BYTES,
        "warn_bytes": int(MAX_UPLOAD_BYTES * WARN_AT_FRACTION),
        "approx_minutes": round(minutes),
        "note": (f"Speaker separation can be run on about {round(minutes)} minutes of "
                 "recording. Longer meetings are still transcribed live and still get a "
                 "record -- only the after-the-fact speaker pass has this limit."),
    }

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


# ── Chunked recording (rule 113) ────────────────────────────────────────────
#
# The browser used to hold every MediaRecorder chunk in a JavaScript array and
# upload the lot at the end. Two failures followed from that, and the code
# claimed the opposite of both: the comment beside `rec.start(5000)` said a
# timeslice meant "a crash costs the last few seconds", when in fact a crashed
# tab cost the ENTIRE recording, because nothing had left the browser; and the
# array grew for the whole meeting, in memory, on a laptop already running
# Ollama and ComfyUI.
#
# So chunks are written here as they arrive. What makes it safe:
#
#   * ORDER IS THE CONTAINER. A WebM stream from one MediaRecorder is a header
#     chunk followed by continuation clusters -- the later chunks are NOT
#     independently decodable files, and concatenating them out of order
#     produces something no decoder will read. `expected_seq` therefore refuses
#     anything but the next piece, rather than accepting it into the wrong
#     place.
#   * A REPEAT IS NOT AN ERROR. A retried upload after a dropped connection
#     re-sends a sequence already written; it is acknowledged and discarded, so
#     a flaky network cannot duplicate audio into the file.
#   * FINALISING IS EXPLICIT. A part file is not a recording until it is
#     finalised, so an interrupted meeting cannot leave something that looks
#     complete and is not.

PART_SUFFIX = ".part"


def _part_path(session_id: str, recording_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", recording_id or "")[:40] or "recording"
    return session_dir(session_id) / f"{safe}{PART_SUFFIX}"


def append_chunk(session_id: str, recording_id: str, seq: int, data: bytes) -> dict:
    """Append one recorded chunk. Returns what has actually been saved so far."""
    path = _part_path(session_id, recording_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state_path = path.with_suffix(".seq")
    try:
        written = int(state_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        written = -1

    if seq <= written:
        # Already have it. Acknowledged, not applied -- see the dedup note above.
        return {"saved": True, "duplicate": True, "next_seq": written + 1,
                "bytes": path.stat().st_size if path.exists() else 0}
    if seq != written + 1:
        raise AudioError(
            f"That piece of the recording arrived out of order (expected {written + 1}, "
            f"got {seq}). The recording is a single stream and its pieces are not "
            "interchangeable, so it was refused rather than written into the wrong place.")

    size = path.stat().st_size if path.exists() else 0
    if size + len(data) > MAX_UPLOAD_BYTES:
        raise AudioError(
            f"This recording has reached the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit "
            f"({upload_budget()['approx_minutes']} minutes or so). What has been recorded "
            "so far is safe and can still have its speakers worked out; the meeting, the "
            "transcript and the report are all unaffected.")
    with path.open("ab") as fh:
        fh.write(data)
    state_path.write_text(str(seq), encoding="utf-8")
    return {"saved": True, "duplicate": False, "next_seq": seq + 1,
            "bytes": path.stat().st_size}


def finalize_recording(session_id: str, recording_id: str,
                       filename: str = "meeting.webm") -> Path:
    """Turn the accumulated part file into THE recording for this meeting."""
    path = _part_path(session_id, recording_id)
    if not path.exists() or path.stat().st_size == 0:
        raise AudioError("Nothing was recorded, so there is no recording to finalise.")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        suffix = ".webm"
    folder = session_dir(session_id)
    final = folder / f"meeting{suffix}"
    for old in folder.iterdir():
        if old.is_file() and old != path and old.suffix.lower() in ACCEPTED_SUFFIXES:
            try:
                old.unlink()
            except OSError:
                pass
    path.replace(final)
    seq = path.with_suffix(".seq")
    try:
        seq.unlink()
    except OSError:
        pass
    return final


def recording_progress(session_id: str, recording_id: str) -> dict:
    """What the server actually holds -- which is the only honest 'saved' claim."""
    path = _part_path(session_id, recording_id)
    state_path = path.with_suffix(".seq")
    try:
        written = int(state_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        written = -1
    return {"next_seq": written + 1,
            "bytes": path.stat().st_size if path.exists() else 0,
            "finalized": recording_path(session_id) is not None}


# The request contract for the diarising model, in one place so a fixture can
# assert on it (rule 109).
#
# `chunking_strategy` is REQUIRED by `gpt-4o-transcribe-diarize` for any input
# longer than 30 seconds -- checked against OpenAI's Create transcription
# reference on 2026-09-09. It was simply absent, so every real meeting (all of
# which are longer than 30 seconds) was sent a request the API rejects, and the
# only recordings that could ever have worked were the ones too short to be
# worth separating. A stub that returns segments regardless of what it was sent
# cannot catch this, which is why the fixture asserts on the FIELDS.
def _diarize_fields(model: str) -> dict:
    return {
        "model": model,
        "response_format": "diarized_json",
        "chunking_strategy": "auto",
    }

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
                data=_diarize_fields(model or DIARIZE_MODEL),
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
