"""The late-night rule and the greeting read the same clock: the customer's own time zone, as her browser reports it.

Seen in an independent rescore: with no RULE_FIXED_HOUR the rule used America/New_York, so a judge in Asia saw "Good
afternoon" on the screen and "It's late at night, so let's double-check" on the card. The browser now sends its IANA
time-zone name; the server checks it is a real zone name and works the hour out from its own clock. A client hour is
never accepted, and anything that isn't a valid zone name falls back to CUSTOMER_TIMEZONE.

These tests don't pin the hour: they look up, right now, a zone where it is night and one where it is day.
"""
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from starlette.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.kernel.rules import valid_timezone  # noqa: E402
from tests.test_requirements import Base  # noqa: E402

ZONES = ["Pacific/Kiritimati", "Pacific/Auckland", "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Asia/Dubai", "Europe/Moscow",
         "Europe/Berlin", "Europe/London", "Atlantic/Azores", "America/Sao_Paulo", "America/New_York", "America/Chicago",
         "America/Denver", "America/Los_Angeles", "Pacific/Honolulu", "Pacific/Pago_Pago"]


def night(hour):
    return hour >= settings.night_start or hour < settings.night_end


def zone_where(want_night):
    for z in ZONES:
        now = datetime.now(ZoneInfo(z))
        if night(now.hour) == want_night and now.minute < 58 and night((now.hour + 1) % 24) == want_night:
            return z
    raise unittest.SkipTest("no zone found")   # can't happen with zones spread over the whole day


class TestZoneName(unittest.TestCase):
    def test_real_zone_names_pass(self):
        for z in ("Asia/Singapore", "America/New_York", "UTC", "America/Argentina/Buenos_Aires", "Etc/GMT+8"):
            with self.subTest(z=z):
                self.assertEqual(valid_timezone(z), z)

    def test_anything_else_is_refused(self):
        for z in (None, "", 14, "14", "../../etc/passwd", "Asia/../Tokyo", "Not/AZone", "Asia/Singapore\n", "A" * 100,
                  "/Asia/Tokyo", "Asia/Tokyo ", {"tz": "UTC"}):
            with self.subTest(z=z):
                self.assertIsNone(valid_timezone(z))


class TestOneClock(Base):
    def setUp(self):
        super().setUp()
        p = patch.object(settings, "fixed_hour", None)      # no pinned hour: the real clock
        p.start()
        self.addCleanup(p.stop)

    def night_rule(self, tz):
        self.setUp()
        self.s.timezone = tz
        self.turn("Send 20 to my son")
        return "R20_NIGHT" in self.s.current_draft.risk.rule_hits

    def test_night_where_she_is_counts_and_day_where_she_is_does_not(self):
        self.assertTrue(self.night_rule(zone_where(True)))
        self.assertFalse(self.night_rule(zone_where(False)))

    def test_no_zone_from_the_browser_uses_the_configured_one(self):
        for want in (True, False):
            z = zone_where(want)
            with self.subTest(zone=z), patch.object(settings, "customer_timezone", z):
                self.assertEqual(self.night_rule(""), want)


class TestBrowserSendsItsZone(unittest.TestCase):
    def setUp(self):
        from app.server import app
        self.c = TestClient(app, base_url="http://localhost")
        self.sid = self.c.post("/api/reset", json={"world": True}).json()["session_id"]

    def session(self):
        from app import runtime
        return runtime.sessions.get(self.sid)

    def test_a_valid_zone_is_kept_for_the_session(self):
        self.c.post("/api/chat", json={"session_id": self.sid, "text": "hello", "tz": "Asia/Singapore"})
        self.assertEqual(self.session().timezone, "Asia/Singapore")

    def test_an_invalid_zone_or_an_hour_is_ignored(self):
        self.c.post("/api/chat", json={"session_id": self.sid, "text": "hello", "tz": "Asia/Tokyo"})
        for bad in ("../etc", "Mars/Olympus", 3, "", None):
            self.c.post("/api/chat", json={"session_id": self.sid, "text": "hello", "tz": bad, "hour": 3})
            self.assertEqual(self.session().timezone, "Asia/Tokyo")

    def test_the_card_and_the_screen_agree(self):
        z = zone_where(True)
        with patch.object(settings, "fixed_hour", None), patch.object(settings, "customer_timezone", zone_where(False)):
            card = self.c.post("/api/chat", json={"session_id": self.sid, "text": "Send 20 to my son", "tz": z}).json()["ui"]["card"]
        self.assertIn("late at night", " ".join(card["reasons"]))
