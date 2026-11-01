"""Global settings: all read from environment variables / .env. Falls back to offline mode when there is no key."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv is optional
    pass

ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _hosts() -> list[str]:
    hosts = [h.strip().lower() for h in _f("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]
    fly = _f("FLY_APP_NAME", "")
    for h in [_f("PUBLIC_HOST", ""), _f("RENDER_EXTERNAL_HOSTNAME", ""), _f("RAILWAY_PUBLIC_DOMAIN", ""),
              _f("SPACE_HOST", ""), f"{fly}.fly.dev" if fly else ""]:
        h = h.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
        if h and h not in hosts:
            hosts.append(h)
    return hosts


_DEV_SECRET = "dev-only-change-me"
_PLACEHOLDER_SECRETS = {_DEV_SECRET.encode(), b"change-me-to-a-long-random-string", b""}


def secret_problem(secret: bytes, *, public_demo: bool, mode: str) -> str:
    """The development secret is fine on one person's own machine with the simulator, and nowhere else."""
    if secret in _PLACEHOLDER_SECRETS and (public_demo or mode != "simulator"):
        return "SIGNING_SECRET must be set to a long random value on a shared server or outside the simulator."
    return ""


class Settings:
    ledger_path = Path(_f("LEDGER_PATH", str(ROOT / "data" / "ledger.sqlite3")))
    # ---- The wallet ----
    wallet_currency = _f("WALLET_CURRENCY", "USD").upper()    # one place; fmt_money and all copy read it
    allowance_cents = int(float(_f("WALLET_ALLOWANCE", "1500")) * 100)   # the limit she and her family set; not a PayPal balance
    scam_hotline_country = _f("SCAM_HOTLINE_COUNTRY", "US").upper()
    customer_timezone = _f("CUSTOMER_TIMEZONE", "America/New_York")   # "late at night" is the customer's night
    public_base_url = _f("PUBLIC_BASE_URL", "") or _f("RENDER_EXTERNAL_URL", "")   # where PayPal sends the buyer back to

    # ---- Model (any OpenAI-compatible API; there is no vendor default) ----
    # offline = built-in rule "model": needs no key, and is the fallback when the online model is unavailable
    llm_provider = _f("LLM_PROVIDER", "offline")          # offline | openai_compat
    llm_base_url = _f("LLM_BASE_URL", "")
    llm_api_key = _f("LLM_API_KEY", "")
    llm_model = _f("LLM_MODEL", "")                       # use a model name your provider actually offers
    llm_judge_model = _f("LLM_JUDGE_MODEL", "")           # model for the scam assessment; empty = same as the main model
    # model for the independent verifier (second engine); empty = the judge model, else the main model.
    # Using a DIFFERENT model here makes the two engines more independent.
    llm_verifier_model = _f("LLM_VERIFIER_MODEL", "")
    planner = _f("PLANNER", "model").lower()            # model: the model writes the plan, code checks it; rules: regex splitter
    llm_timeout = float(_f("LLM_TIMEOUT", "30"))
    llm_max_tokens = int(_f("LLM_MAX_TOKENS", "600"))      # cap per call: a runaway generation can't stall a turn
    # A forced tool call (tool_choice=required) slower than this switches forced calls off for this model server;
    # the harness nudge + rule-based fallback still keep the model honest
    llm_required_slow = float(_f("LLM_REQUIRED_SLOW_SECONDS", "8"))
    # off = ask Qwen-style (vLLM) servers to skip the hidden "thinking" step: ~3x faster per call.
    # Leave empty for providers that don't accept chat_template_kwargs.
    llm_thinking = _f("LLM_THINKING", "").lower()

    # ---- Agent Harness ----
    max_steps = int(_f("AGENT_MAX_STEPS", "6"))           # step cap for constrained ReAct
    max_history_turns = int(_f("AGENT_MAX_HISTORY", "8"))

    # ---- Risk parameters (example values, configurable) ----
    single_txn_hard_limit = float(_f("RULE_HARD_LIMIT", "5000"))   # above this: blocked outright
    user_high_threshold = float(_f("RULE_HIGH_THRESHOLD", "500"))  # above this: cooling-off period (enhanced protection)
    user_high_threshold_standard = float(_f("RULE_HIGH_THRESHOLD_STANDARD", "1200"))  # same, standard protection
    guardian_threshold = float(_f("GUARDIAN_THRESHOLD", "300"))    # a first payment to someone new above this: the family co-approves
    unknown_alert_minutes = int(_f("PAYPAL_UNKNOWN_ALERT_MIN", "10"))  # an unresolved outcome older than this alerts the family
    # Lowering protection (enhanced -> standard) is signed on the device and only takes effect after this delay
    protection_delay = int(_f("PROTECTION_LOWER_DELAY", "120"))   # demo: 2 min; a real wallet: 86400 (24 h)
    cooldown_seconds = int(_f("RULE_COOLDOWN_SECONDS", "20"))      # 20 s for the demo, e.g. 600 in production
    velocity_window_min = int(_f("RULE_VELOCITY_WINDOW_MIN", "10"))
    velocity_max = int(_f("RULE_VELOCITY_MAX", "5"))
    night_start, night_end = 23, 6
    # pin the "current hour" during evaluation so running it late at night doesn't change risk levels
    fixed_hour = int(os.environ["RULE_FIXED_HOUR"]) if os.getenv("RULE_FIXED_HOUR") else None

    # ---- Signing ----
    signing_secret = (_f("SIGNING_SECRET", _DEV_SECRET) or _DEV_SECRET).encode()
    auth_token_ttl = int(_f("AUTH_TOKEN_TTL", "120"))
    # device_key = the browser signs every payment with its own non-extractable P-256 key (default)
    # mock       = also accept the old "demo-fingerprint" string (only for quick scripts; never for the demo)
    auth_mode = _f("AUTH_MODE", "device_key")
    challenge_ttl = int(_f("CHALLENGE_TTL", "120"))
    # Hosts the web server answers to (DNS-rebinding protection). Add your LAN name/IP here to demo from another device.
    # On a hosting platform its public hostname is added automatically (Render, Railway, Fly.io, Hugging Face Spaces),
    # or set PUBLIC_HOST yourself (e.g. your own server's domain).
    allowed_hosts = _hosts()
    # A shared online demo: every browser is its own demo customer, and nobody can reset everyone's world
    public_demo = _f("PUBLIC_DEMO", "0").lower() in ("1", "true", "yes")
    # How many proxies of our own sit in front of the server (each appends to X-Forwarded-For). 0 = ignore the header.
    trusted_proxy_hops = int(_f("TRUSTED_PROXY_HOPS", "0") or 0)
    # The demo's default signer: a simulated fingerprint step unlocks a non-extractable browser key that signs the
    # exact card. Real passkeys (Face ID / Touch ID / Windows Hello) are an
    # opt-in in Settings. Set ALLOW_BROWSER_KEY=0 to require a real passkey for every payment.
    allow_browser_key = _f("ALLOW_BROWSER_KEY", "1").lower() in ("1", "true", "yes")
    draft_ttl = int(_f("DRAFT_TTL", "600"))

    # ---- Speech recognition. Default: the browser's own. Optional: any OpenAI-compatible /audio/transcriptions ----
    asr_provider = _f("ASR_PROVIDER", "browser").lower()     # browser | openai_compat
    asr_base_url = _f("ASR_BASE_URL", "")                    # empty = LLM_BASE_URL
    asr_api_key = _f("ASR_API_KEY", "")                      # empty = LLM_API_KEY
    asr_model = _f("ASR_MODEL", "")

    # ---- Storage ----
    audit_path = Path(_f("AUDIT_PATH", str(ROOT / "data" / "audit.jsonl")))
    voice_dir = Path(_f("VOICE_DIR", str(ROOT / "data" / "voice")))   # recordings kept for the audit trail
    # Enrolled passkeys (public keys only). Kept across demo resets, like a phone enrolled with the wallet.
    passkey_path = Path(_f("PASSKEY_PATH", str(ROOT / "data" / "passkeys.json")))

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider == "openai_compat" and bool(self.llm_api_key and self.llm_base_url and self.llm_model)

    @property
    def asr_enabled(self) -> bool:
        return self.asr_provider == "openai_compat" and bool(self.asr_model) and bool(self.asr_base_url or self.llm_base_url)


settings = Settings()
