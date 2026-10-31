"""The wallet tools (app/wallet/tools/): saved people, limits, transfers, invoices. Each is checked end to end through the
agent (offline planner), the kernel and the execution gateway: money moves only after a signature; account changes that
raise risk are signed too; protective ones are instant.
"""
import base64
import os

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_requirements import Base  # noqa: E402
from app.kernel import p256  # noqa: E402
from app.kernel.executor import KernelError  # noqa: E402
from app.models import Phase, RiskLevel  # noqa: E402

TOM = "Add a new person Tom Reed, email tom.reed@mail.example"


class ApiBase(Base):
    def setUp(self):
        super().setUp()
        self.key, self.jwk = p256.generate_keypair()
        self.kernel.register_key(self.s, self.jwk)

    def sign(self, payload: str) -> dict:
        return {"payload": payload, "signature": base64.b64encode(p256.sign(self.key, payload.encode())).decode()}

    def pay(self):
        """Sign and execute the current draft, like the confirmation card does."""
        d = self.s.current_draft
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.sign(self.kernel.issue_challenge(self.s, d.draft_id, d.digest)))
        return self.kernel.commit(self.s, d.draft_id, tok)

    def bal(self):
        return self.wallet.allowance_left("u_lee")

    def apis(self, r):
        return [t["label"] for t in r.trace if t["kind"] == "api"]


class TestAllowance(ApiBase):
    def test_says_what_it_is(self):
        r = self.turn("What's my balance?")
        self.assertIn("Star · allowance", self.apis(r))
        self.assertIn("$1,500.00", r.reply)
        self.assertIn("can't see your PayPal balance", r.reply)

    def test_over_the_allowance_is_blocked_and_says_why(self):
        self.turn("Send 2000 to my son")
        d = self.s.current_draft
        self.assertIn("R03_OVER_ALLOWANCE", d.risk.rule_hits)
        self.assertEqual(d.risk.level, RiskLevel.BLOCK)
        self.assertIn("limit you and your family set", " ".join(d.risk.reasons))

    def test_spending_reserves_the_allowance(self):
        self.turn("Send 50 to my daughter")
        self.pay()
        self.assertEqual(self.bal(), 150000 - 5000)


class TestBills(ApiBase):
    def test_amount_comes_from_the_invoice(self):
        self.turn("Pay the power bill")
        d = self.s.current_draft
        self.assertEqual((d.kind, d.payee_id, d.amount_cents, d.grounding["amount_source"]), ("bill", "p_power", 8640, "bill"))
        self.assertEqual(d.invoice_number, "RP-2026-1001")

    def test_the_model_cannot_change_a_bills_amount(self):
        self.s.messages.append({"role": "user", "content": "Pay the power bill"})
        res = self.gw.call(self.s, "draft_bill_payment", {"invoice_id": "inv_power_oct", "amount": 1})
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.s.current_draft.amount_cents, 8640)      # the stray argument is ignored

    def test_unknown_invoice_rejected(self):
        res = self.gw.call(self.s, "draft_bill_payment", {"invoice_id": "nope"})
        self.assertFalse(res["ok"])


class TestLimits(ApiBase):
    def test_lowering_is_instant_and_enforced(self):
        self.turn("Lower my daily limit to 500")
        self.assertEqual(self.wallet.limits("u_lee")["daily_transfer_cents"], 50000)
        self.s = type(self.s)(session_id="h2")
        self.kernel.register_key(self.s, p256.generate_keypair()[1])
        self.turn("Send 600 to my daughter")
        self.assertIn("R13_DAILY_LIMIT", self.s.current_draft.risk.rule_hits)
        self.assertEqual(self.s.current_draft.risk.level, RiskLevel.BLOCK)

    def test_raising_is_signed_and_delayed(self):
        r = self.turn("Raise my daily limit to 8000")
        self.assertEqual(r.ui["type"], "change")
        cid = r.ui["card"]["change_id"]
        self.kernel.apply_change(self.s, cid, self.sign(self.kernel.change_challenge(self.s, cid)))
        lim = self.wallet.limits("u_lee")
        self.assertEqual(lim["daily_transfer_cents"], 100000)          # not yet
        self.assertEqual(lim["pending"]["to_cents"], 800000)

    def test_bills_do_not_count(self):
        self.turn("Lower my daily limit to 50")
        self.s = type(self.s)(session_id="h2b")
        d = self.model_drafts("Pay the power bill", "draft_bill_payment", {"invoice_id": "inv_power_oct"})
        self.assertNotIn("R13_DAILY_LIMIT", d.risk.rule_hits)


class TestPayees(ApiBase):
    def test_add_person_is_grounded_and_signed(self):
        r = self.turn(TOM)
        self.assertEqual(r.ui["type"], "change")
        self.assertIn(["PayPal email", "tom.reed@mail.example"], r.ui["card"]["rows"])
        cid = r.ui["card"]["change_id"]
        self.kernel.apply_change(self.s, cid, self.sign(self.kernel.change_challenge(self.s, cid)))
        p = next(p for p in self.wallet.payees("u_lee") if p["name"] == "Tom Reed")
        self.assertEqual(p["masked_account"], "t***@mail.example")
        # the first transfer to a brand-new person waits and is double-checked
        self.s = type(self.s)(session_id="h3")
        d = self.model_drafts("send 50 to Tom Reed", "draft_transfer", {"payee_id": p["payee_id"], "amount": 50})
        self.assertIn("R10_NEW_PAYEE", d.risk.rule_hits)

    def _new_person_draft(self, amount=200):
        r = self.turn(TOM)
        cid = r.ui["card"]["change_id"]
        self.kernel.apply_change(self.s, cid, self.sign(self.kernel.change_challenge(self.s, cid)))
        p = next(p for p in self.wallet.payees("u_lee") if p["name"] == "Tom Reed")
        self.s = type(self.s)(session_id="h4")
        self.kernel.register_key(self.s, self.jwk)
        return self.model_drafts(f"send {amount} to Tom Reed", "draft_transfer", {"payee_id": p["payee_id"], "amount": amount})

    def test_enhanced_asks_whether_paying_a_new_person_is_their_own_idea(self):
        d = self._new_person_draft()
        self.assertEqual(d.risk.verify_kind, "new_payee")
        self.assertIn("Tom Reed", d.risk.verify_question)
        d.cooling_until = 0
        self.kernel._refresh_cooling(self.s, d)
        with self.assertRaises(KernelError):                     # no "yes", no signature
            self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=False)
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=True)
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.sign(ch), verify_answer=True)
        # A new person is UNCLAIMED until they take it: not "success" yet, and she can still stop it.
        self.assertEqual(self.kernel.commit(self.s, d.draft_id, tok)["state"], "UNCLAIMED")

    def test_standard_protection_does_not_ask(self):
        self.wallet.protection("u_lee")["level"] = "standard"
        d = self._new_person_draft()
        self.assertIsNone(d.risk.verify_kind)
        self.assertIn("R10_NEW_PAYEE", d.risk.rule_hits)          # still waits a little

    def test_email_must_be_what_the_user_said(self):
        self.s.messages.append({"role": "user", "content": "add my friend Tom Reed, tom.reed@mail.example"})
        res = self.gw.call(self.s, "request_add_payee", {"name": "Tom Reed", "email": "attacker@mail.example"})
        self.assertFalse(res["ok"])
        self.assertIsNone(self.s.pending_change)

    def test_no_risk_raising_changes_in_a_scam_conversation(self):
        self.s.scam_level = RiskLevel.HIGH
        self.s.messages.append({"role": "user", "content": "add person Tom Reed tom.reed@mail.example"})
        with self.assertRaises(KernelError):
            self.kernel.request_change(self.s, "add_payee", {"name": "Tom Reed", "email": "tom.reed@mail.example"})


class TestToolOffering(ApiBase):
    def test_topic_tools_only_when_relevant(self):
        self.s.messages.append({"role": "user", "content": "send 50 to my daughter"})
        names = {t["function"]["name"] for t in self.gw.schemas(self.s)}
        self.assertNotIn("set_daily_limit", names)
        self.assertNotIn("list_disputes", names)
        self.s.messages.append({"role": "user", "content": "and what is my daily limit?"})
        self.assertIn("set_daily_limit", {t["function"]["name"] for t in self.gw.schemas(self.s)})

    def test_every_api_call_shows_in_the_trace(self):
        r = self.turn("Send 100 to my son")
        self.assertEqual(self.apis(r), ["Star · saved people", "PayPal Payouts · draft only, nothing is sent"])


class _ClaimsItsDone:
    """A model that says the person was added although nobody signed anything (seen with a live model)."""
    name = "stub"

    def chat(self, messages, tools=None, **_):
        return {"role": "assistant", "content": "Thanks for confirming! Tom Reed has been added to your payees.",
                "tool_calls": []}


class TestNoFalseClaims(ApiBase):
    def test_unsigned_change_is_never_reported_as_done(self):
        from app.agent.loop import Agent
        self.turn(TOM)
        self.assertIsNotNone(self.s.pending_change)
        r = Agent(self.wallet, self.gw, self.audit, _ClaimsItsDone()).run_turn(self.s, "Yes, that's right")
        self.assertIn("Nothing has changed yet", r.reply)
        self.assertFalse(any(p["name"] == "Tom Reed" for p in self.wallet.payees("u_lee")))


class TestVagueAmounts(ApiBase):
    """"Half of it": code computes the amount, never the model. "Half of my money": Star can't see a balance, so it asks."""

    def test_half_of_my_money_is_never_turned_into_an_amount(self):
        r = self.turn("Send half of my money to my daughter")
        self.assertIsNone(self.s.current_draft)
        self.assertTrue(r.reply.rstrip().endswith("?"))

    def test_wrong_share_is_blocked(self):
        # the live model once used the whole amount for "half of it"
        d = self.model_drafts("Send 300 to my son and half of it to my daughter", "draft_transfer",
                              {"payee_id": "p_daughter", "amount": 300})
        self.assertEqual(d.grounding["amount_source"], "user")      # 300 was said: the plan handles which step it is
        self.s = type(self.s)(session_id="h5")
        d = self.model_drafts("Send half of it to my daughter, it was 200", "draft_transfer", {"payee_id": "p_daughter", "amount": 150})
        self.assertEqual(d.grounding["amount_source"], "derived mismatch")
        self.assertIn("R53_WRONG_SHARE", d.risk.rule_hits)
        self.assertEqual(d.risk.level, RiskLevel.BLOCK)

    def test_half_of_it_uses_the_amount_just_said(self):
        self.turn("Send 200 to my son")
        self.assertEqual(self.s.current_draft.amount_cents, 20000)
        self.pay()
        self.turn("Now send half of it to my daughter")
        d = self.s.current_draft
        self.assertEqual((d.payee_id, d.amount_cents), ("p_daughter", 10000))
        self.assertEqual(d.grounding["amount_source"], "derived")

    def test_yes_after_a_proposal_prepares_the_card(self):
        self.s.amount_hint = None
        self.s.messages.append({"role": "user", "content": "Can you send my daughter a grand?"})
        self.s.messages.append({"role": "assistant", "content": "A grand is $1,000.00. Shall I prepare it?"})
        r = self.turn("Yes, that's right.")
        self.assertIn(r.ui["type"], ("confirm", "cooldown"))          # a card, not more talk
        self.assertEqual(self.s.current_draft.amount_cents, 100000)


class TestSpeechSlips(ApiBase):
    """Seen live: "a grand" came through speech recognition as "a grant", "a grind" and even "Agree!".
    The model works out what was meant; the kernel only checks that reading, it never rewrites the user's words."""

    def test_a_grant_is_read_as_a_grand_and_shown_on_the_card(self):
        r = self.turn("I want to send a grant to my daughter.")
        d = self.s.current_draft
        self.assertEqual((d.payee_id, d.amount_cents, d.grounding["amount_source"]), ("p_daughter", 100000, "heard"))
        self.assertIn("“a grant”", r.ui["card"]["amount_note"])
        self.assertEqual(self.s.messages[0]["content"], "I want to send a grant to my daughter.")  # not rewritten

    def test_unsure_reading_is_asked_then_confirmed(self):
        self.turn("I want to send money to my daughter")
        r = self.turn("Agree!")
        self.assertIn("Did you mean a grand", r.reply)
        self.assertIsNone(self.s.current_draft)
        r = self.turn("Yes")
        d = self.s.current_draft
        self.assertEqual((d.amount_cents, d.grounding["amount_source"]), (100000, "confirmed"))
        self.assertIn("you said yes", r.ui["card"]["amount_note"].lower())

    def test_a_reading_that_adds_a_number_is_not_accepted(self):
        d = self.model_drafts("Send a grant to my daughter", "draft_transfer",
                              {"payee_id": "p_daughter", "amount": 10000, "misheard": "a grant", "meant": "ten grand"})
        self.assertEqual(d.grounding["amount_source"], "not said")

    def test_misheard_words_must_be_in_the_transcript(self):
        d = self.model_drafts("Send money to my daughter", "draft_transfer",
                              {"payee_id": "p_daughter", "amount": 1000, "misheard": "a grant", "meant": "a grand"})
        self.assertEqual(d.grounding["amount_source"], "not said")

    def test_offline_stand_in_leaves_real_grants_alone(self):
        from app.agent.textparse import fix_asr
        for t in ("I got a grant from the government", "Can I apply for a grant?", "Pay my grandson 50", "Agree!"):
            self.assertEqual(fix_asr(t)[0], t)


class TestOddAnswerPointer(ApiBase):
    def test_state_points_the_model_at_an_answer_without_a_number(self):
        from app.agent.prompts import state_summary
        self.s.messages += [{"role": "user", "content": "send money to my daughter"},
                            {"role": "assistant", "content": "How much would you like to send?"},
                            {"role": "user", "content": "Agree!"}]
        self.assertIn('answered "Agree!", which has no number', state_summary(self.s, self.wallet))
        self.s.messages[-1]["content"] = "300"
        self.assertNotIn("which has no number", state_summary(self.s, self.wallet))

    def test_model_may_ask_back_without_a_tool(self):
        from app.agent.loop import Agent

        class AsksBack:
            name = "stub"

            def chat(self, messages, tools=None, **_):
                return {"role": "assistant", "content": "Did you mean a grand, $1,000?", "tool_calls": []}
        self.turn("I want to send money to my daughter")
        r = Agent(self.wallet, self.gw, self.audit, AsksBack()).run_turn(self.s, "Agree!")
        self.assertEqual(r.reply, "Did you mean a grand, $1,000?")
        self.assertFalse(any(t["kind"] == "fallback" for t in self.s.trace))


class TestPhaseStillBlocksDrafting(ApiBase):
    def test_frozen_wallet_blocks_everything_but_refunds(self):
        self.kernel.freeze(self.s, "the customer")
        d = self.model_drafts("Send 50 to my daughter", "draft_transfer", {"payee_id": "p_daughter", "amount": 50})
        self.assertIn("R14_WALLET_FROZEN", d.risk.rule_hits)
        self.assertEqual(self.s.phase, Phase.BLOCKED)
