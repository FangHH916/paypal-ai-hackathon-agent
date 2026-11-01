"""The scam hotline Star points to. One configurable table, one place in all the copy (SCAM_HOTLINE_COUNTRY).

US: the National Elder Fraud Hotline, DOJ Office for Victims of Crime, checked at
https://ovc.ojp.gov/program/stop-elder-fraud/providing-help-restoring-hope during planning.
"""
from __future__ import annotations

from app.config import settings

HOTLINES = {
    "US": {"name": "the National Elder Fraud Hotline", "number": "833-372-8311", "spoken": "833-FRAUD-11", "verified": True},
}


def hotline() -> dict:
    return HOTLINES.get(settings.scam_hotline_country, HOTLINES["US"])


def hotline_words() -> str:
    h = hotline()
    return f"{h['name']} on {h['spoken']}"
