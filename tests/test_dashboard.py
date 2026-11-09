"""The Dashboard (AG Studio) runs in a frame of its own; the app around it keeps its strict policy.

AG Studio writes inline style attributes whose values change with the data, so they can't be pinned by hash like the
grid's. Only the frame allows inline styles; its scripts are still only this origin's, and only this origin can frame
it. The frame reads the activity rows and nothing else.
"""
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from app import server
from app.config import settings
from app.security import CSP, DASHBOARD_CSP

WEB = Path(__file__).resolve().parent.parent / "web"


class TestDashboardFrame(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(server.app, base_url="http://localhost")

    def test_the_frame_has_its_own_policy_and_only_this_origin_can_frame_it(self):
        r = self.c.get("/dashboard-frame")
        self.assertEqual(r.status_code, 200)
        csp = r.headers["content-security-policy"]
        self.assertEqual(csp, DASHBOARD_CSP)
        self.assertIn("script-src 'self';", csp)
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertEqual(r.headers["x-frame-options"], "SAMEORIGIN")

    def test_the_app_keeps_its_strict_style_policy(self):
        r = self.c.get("/")
        self.assertNotIn("unsafe-inline", r.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", CSP)

    def test_the_bundle_is_served_from_this_origin_and_gzipped_when_asked(self):
        self.assertTrue((WEB / "dashboard" / "star-dashboard.js").is_file())
        r = self.c.get("/dashboard/star-dashboard.js", headers={"accept-encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/javascript"))
        plain = self.c.get("/dashboard/star-dashboard.js", headers={"accept-encoding": "identity"})
        self.assertNotIn("content-encoding", plain.headers)

    def test_the_licence_comes_from_the_environment_never_the_repository(self):
        sid = self.c.post("/api/reset", json={}).json()["session_id"]
        old = settings.ag_studio_license
        try:
            settings.ag_studio_license = ""
            self.assertEqual(self.c.get(f"/api/dashboard/config?session_id={sid}").json()["license"], "")
            settings.ag_studio_license = "test-licence"
            self.assertEqual(self.c.get(f"/api/dashboard/config?session_id={sid}").json()["license"], "test-licence")
        finally:
            settings.ag_studio_license = old
        bundle = (WEB / "dashboard" / "star-dashboard.js").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn("[TRIAL]_this_", bundle)
        self.assertNotIn("Enterprise_key_{AG-", bundle)

    def test_the_config_needs_a_session(self):
        self.assertNotEqual(self.c.get("/api/dashboard/config?session_id=nope").status_code, 200)


if __name__ == "__main__":
    unittest.main()
