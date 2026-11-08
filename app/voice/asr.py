"""Optional server-side speech recognition.

Off by default: the app uses the browser's own speech recognition, and then no recordings exist and the UI says so.
Two server options, both keeping each recording under its SHA-256 and linking it to the words (kernel/voice.py):
- ASR_PROVIDER=openai_compat: any OpenAI-compatible POST {base}/audio/transcriptions.
- ASR_PROVIDER=tencent: Tencent Cloud one-sentence recognition (SentenceRecognition, API 3.0, TC3-HMAC-SHA256),
  with TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY. Audio up to 60 s; the browser sends 16 kHz mono WAV.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone

import requests

from app.config import settings

TENCENT_HOST = "asr.tencentcloudapi.com"
TENCENT_SERVICE = "asr"
TENCENT_VERSION = "2019-06-14"
TENCENT_MAX_BYTES = 3 * 1024 * 1024 * 3 // 4     # 3 MB after base64


class ASRError(Exception):
    pass


def build_prompt(payees: list[dict]) -> str:
    """Names the recogniser should expect. Whisper-style services take a free-text hint."""
    names = {p["name"] for p in payees} | {"PayPal", "Star", "refund", "allowance"}
    return ", ".join(sorted(names))[:400]


def recognize(wav_bytes: bytes, prompt: str = "") -> dict:
    if not settings.asr_enabled:
        raise ASRError("Server speech recognition is not configured")
    if settings.asr_provider == "tencent":
        return _tencent(wav_bytes, prompt)
    return _openai_compat(wav_bytes, prompt)


def _openai_compat(wav_bytes: bytes, prompt: str) -> dict:
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
    return {"text": text.strip(), "latency_ms": int((time.time() - t0) * 1000), "engine": settings.asr_engine}


# ---- Tencent Cloud ----

def hotwords(prompt: str) -> str:
    """Tencent's temporary hotword list ("word|weight", comma-separated, no spaces) from the names in the prompt.
    Multi-word names are split into words, since a hotword may not contain a space."""
    words: list[str] = []
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", prompt):
        if 1 < len(w) <= 30 and w.lower() not in {x.lower() for x in words}:
            words.append(w)
    return ",".join(f"{w}|10" for w in words[:128])


def tc3_headers(secret_id: str, secret_key: str, action: str, payload: str, timestamp: int, region: str = "",
                host: str = TENCENT_HOST, service: str = TENCENT_SERVICE, version: str = TENCENT_VERSION) -> dict:
    """Tencent Cloud API 3.0 signature (TC3-HMAC-SHA256), signing content-type and host."""
    ctype = "application/json; charset=utf-8"
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")   # UTC, as the spec requires
    canonical = "\n".join(["POST", "/", "", f"content-type:{ctype}\nhost:{host}\n", "content-type;host",
                           hashlib.sha256(payload.encode("utf-8")).hexdigest()])
    scope = f"{date}/{service}/tc3_request"
    to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])

    def _h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k = _h(_h(_h(("TC3" + secret_key).encode("utf-8"), date), service), "tc3_request")
    sig = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"Authorization": f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={sig}",
               "Content-Type": ctype, "Host": host, "X-TC-Action": action, "X-TC-Timestamp": str(timestamp),
               "X-TC-Version": version}
    if region:
        headers["X-TC-Region"] = region
    return headers


def _tencent_call(body: dict) -> dict:
    payload = json.dumps(body, separators=(",", ":"))
    headers = tc3_headers(settings.tencent_secret_id, settings.tencent_secret_key, "SentenceRecognition", payload,
                          int(time.time()), settings.tencent_asr_region)
    try:
        r = requests.post(f"https://{TENCENT_HOST}/", data=payload.encode("utf-8"), headers=headers, timeout=20)
    except requests.RequestException as e:
        raise ASRError(f"Network error: {type(e).__name__}") from e
    try:
        resp = r.json()["Response"]
        if not isinstance(resp, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError) as e:
        raise ASRError("Speech service returned an invalid response. Please retry or type your request.") from e
    return resp


def _tencent(wav_bytes: bytes, prompt: str) -> dict:
    if len(wav_bytes) > TENCENT_MAX_BYTES:
        raise ASRError("That recording is too long. Please say it in under a minute, or type your request.")
    body = {"EngSerViceType": settings.asr_model or "16k_en", "SourceType": 1, "VoiceFormat": "wav",
            "Data": base64.b64encode(wav_bytes).decode("ascii"), "DataLen": len(wav_bytes)}
    words = hotwords(prompt)
    t0 = time.time()
    resp = _tencent_call({**body, "HotwordList": words} if words else body)
    err = resp.get("Error") or {}
    if words and str(err.get("Code", "")).startswith("InvalidParameter"):
        resp = _tencent_call(body)          # the hotwords are a hint only: never let them cost the transcript
        err = resp.get("Error") or {}
    if err:
        code = str(err.get("Code", "Unknown"))[:80]
        raise ASRError(f"Speech service returned an error ({code}). Please retry or type your request.")
    text = resp.get("Result")
    if not isinstance(text, str):
        raise ASRError("Speech service returned an invalid response. Please retry or type your request.")
    return {"text": text.strip(), "latency_ms": int((time.time() - t0) * 1000), "engine": settings.asr_engine}
