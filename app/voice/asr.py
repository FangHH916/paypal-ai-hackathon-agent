"""Optional server-side speech recognition, through any OpenAI-compatible POST {base}/audio/transcriptions.

Off by default: the app uses the browser's own speech recognition, and then no recordings exist and the UI says so.
With ASR_PROVIDER=openai_compat each recording is kept under its SHA-256 and linked to the words (kernel/voice.py).
"""
from __future__ import annotations

import time

import requests

from app.config import settings


class ASRError(Exception):
    pass


def build_prompt(payees: list[dict]) -> str:
    """Names the recogniser should expect. Whisper-style services take a free-text hint."""
    names = {p["name"] for p in payees} | {"PayPal", "Star", "refund", "allowance"}
    return ", ".join(sorted(names))[:400]


def recognize(wav_bytes: bytes, prompt: str = "") -> dict:
    if not settings.asr_enabled:
        raise ASRError("ASR_PROVIDER=openai_compat needs ASR_MODEL and a base URL")
    base = (settings.asr_base_url or settings.llm_base_url).rstrip("/")
    key = settings.asr_api_key or settings.llm_api_key
    t0 = time.time()
    try:
        r = requests.post(f"{base}/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                          files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                          data={"model": settings.asr_model, "language": "en", **({"prompt": prompt} if prompt else {})}, timeout=20)
    except requests.RequestException as e:
        raise ASRError(f"Network error: {type(e).__name__}") from e
    if r.status_code != 200:
        raise ASRError("Speech service returned an error. Please retry or type your request.")
    try:
        text = r.json()["text"]
        if not isinstance(text, str):
            raise ValueError
    except (ValueError, KeyError, TypeError) as e:
        raise ASRError("Speech service returned an invalid response. Please retry or type your request.") from e
    return {"text": text.strip(), "latency_ms": int((time.time() - t0) * 1000), "engine": settings.asr_model}
