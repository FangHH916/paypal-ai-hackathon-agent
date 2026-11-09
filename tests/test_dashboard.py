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



class TestDashboardAssistant(unittest.TestCase):
    """AG Studio's AI assistant reaches Star's model only through /api/dashboard/ai: the server holds the key, fixes the
    model and caps the output; the browser can't choose any of them."""

    def setUp(self):
        self.c = TestClient(server.app, base_url="http://localhost")
        self.sid = self.c.post("/api/reset", json={}).json()["session_id"]
        self.saved = {k: getattr(settings, k) for k in ("llm_provider", "llm_base_url", "llm_api_key", "llm_model")}

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(settings, k, v)

    def connect(self):
        settings.llm_provider, settings.llm_base_url, settings.llm_api_key, settings.llm_model = (
            "openai_compat", "http://model.invalid/v1", "server-key", "server-model")

    def ask(self, **extra):
        body = {"session_id": self.sid, "messages": [{"role": "user", "content": "Who got the most?"}], **extra}
        return self.c.post("/api/dashboard/ai", json=body)

    def test_without_a_model_it_says_so(self):
        settings.llm_provider = "offline"
        r = self.ask()
        self.assertEqual(r.status_code, 503)
        self.assertFalse(self.c.get(f"/api/dashboard/config?session_id={self.sid}").json()["ai"])

    def test_the_server_picks_the_model_holds_the_key_and_caps_the_output(self):
        from unittest import mock
        self.connect()
        seen = {}

        class Reply:
            status_code = 200
            def json(self):
                return {"id": "x", "model": "server-model", "choices": [{"message": {"role": "assistant", "content": "Anna."}}]}

        def post(url, json, timeout, headers):
            seen.update(url=url, body=json, headers=headers)
            return Reply()

        with mock.patch("requests.post", post):
            r = self.ask(model="someone-elses-model", max_tokens=99999, api_key="nope", tools=[{"type": "function", "function": {"name": "view_page"}}])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "Anna.")
        self.assertEqual(seen["url"], "http://model.invalid/v1/chat/completions")
        self.assertEqual(seen["body"]["model"], "server-model")
        self.assertEqual(seen["body"]["max_tokens"], server.AI_MAX_TOKENS)
        self.assertNotIn("api_key", seen["body"])
        self.assertEqual(seen["headers"]["Authorization"], "Bearer server-key")
        self.assertTrue(self.c.get(f"/api/dashboard/config?session_id={self.sid}").json()["ai"])

    def test_it_needs_a_session_and_messages(self):
        self.connect()
        self.assertNotEqual(self.c.post("/api/dashboard/ai", json={"session_id": "nope", "messages": [{"role": "user", "content": "hi"}]}).status_code, 200)
        self.assertEqual(self.c.post("/api/dashboard/ai", json={"session_id": self.sid, "messages": []}).status_code, 400)

    def test_a_huge_request_is_refused_before_it_reaches_the_model(self):
        self.connect()
        r = self.ask(tools=[{"type": "function", "function": {"name": "x" * (600 * 1024)}}])
        self.assertEqual(r.status_code, 413)


if __name__ == "__main__":
    unittest.main()
