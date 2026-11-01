"""Model planner (agent/planner.py): the model writes the plan, the code computes and checks it.

A stub model answers the planner call with a fixed plan (as a real model would) and the offline planner does the
ReAct part, so these run without an API key. The point: phrasings no rule knows ("then give her half that money")
work, the amounts come from signed, verified steps, and a bad plan can't move money.
"""
import json
import os

from unittest.mock import patch

os.environ.setdefault("RULE_FIXED_HOUR", "14")

from tests.test_wallet_tools import ApiBase  # noqa: E402
from app.agent import planner  # noqa: E402
from app.agent.llm import OfflineLLM  # noqa: E402
from app.agent.loop import Agent  # noqa: E402
from app.agent.verifier import Verifier  # noqa: E402
from app.config import settings  # noqa: E402
from app.models import RiskLevel  # noqa: E402


class PlanStub:
    """Answers the planner prompt with `steps`; everything else is the offline planner."""
    name = "stub"

    def __init__(self, steps):
        self.steps, self.off, self.plan_calls = steps, OfflineLLM(), 0

    def chat(self, messages, tools=None, json_mode=False, model=None, **_):
        if messages and messages[0]["content"].startswith("You turn a customer's payment request"):
            self.plan_calls += 1
            return {"role": "assistant", "content": json.dumps({"steps": self.steps}), "tool_calls": []}
        return self.off.chat(messages, tools=tools, json_mode=json_mode)


HALF_THAT_MONEY = [
    {"said": "Send 200 to my son", "request": "Send 200 dollars to my son", "action": "transfer",
     "amount": {"kind": "fixed", "value": 200}},
    {"said": "then give my daughter half that money", "request": "Send money to my daughter", "action": "transfer",
     "amount": {"kind": "share_of_step", "step": 1, "fraction": 0.5}},
]
SAID = "Send 200 to my son, then give my daughter half that money"
FOUR_STEPS = [
    {"said": "I want to send 30 to my daughter", "request": "Send 30 dollars to my daughter", "action": "transfer", "amount": {"kind": "fixed", "value": 30}},
    {"said": "20 to my son", "request": "Send 20 dollars to my son", "action": "transfer", "amount": {"kind": "fixed", "value": 20}},
    {"said": "10 to Ben", "request": "Send 10 dollars to Ben Lee", "action": "transfer", "amount": {"kind": "fixed", "value": 10}},
    {"said": "and half that to Sam", "request": "Send money to Sam Lee", "action": "transfer",
     "amount": {"kind": "share_of_step", "step": 3, "fraction": 0.5}},
]
SAID_FOUR = "I want to send 30 to my daughter, 20 to my son, 10 to Ben, and half that to Sam."


class PlannerBase(ApiBase):
    def use_plan(self, steps):
        self.stub = PlanStub(steps)
        self.agent = Agent(self.wallet, self.gw, self.audit, self.stub)

    def sign_all(self):
        """Sign the current card like the device does (answering a verification question, after any cooling-off)."""
        d = self.s.current_draft
        d.cooling_until = 0
        self.kernel._refresh_cooling(self.s, d)
        ans = True if d.risk.verify_question else None
        ch = self.kernel.issue_challenge(self.s, d.draft_id, d.digest, verify_answer=ans)
        tok = self.kernel.authorize(self.s, d.draft_id, d.digest, self.sign(ch), verify_answer=ans)
        rec = self.kernel.commit(self.s, d.draft_id, tok)
        self.s.settle()
        return rec

    def next_step(self):
        return self.turn(self.s.plan.pop(0))           # the app sends the next queued step after signing


class TestPhrasingsNoRuleKnows(PlannerBase):
    def test_half_that_money_after_the_signed_step(self):
        self.use_plan(HALF_THAT_MONEY)
        r = self.turn(SAID)
        self.assertEqual(r.ui["plan"], ["Send 200 to my son", "then give my daughter half that money"])
        d = self.s.current_draft
        self.assertEqual((d.payee_id, d.amount_cents), ("p_son", 20000))
        self.sign_all()
        r = self.next_step()
        d = self.s.current_draft
        self.assertEqual((d.payee_id, d.amount_cents), ("p_daughter", 10000))
        self.assertEqual(d.grounding["amount_source"], "derived")
        self.assertIn("half of the $200.00 in step 1", r.ui["card"]["amount_note"])
        self.assertEqual(d.you_said, SAID)


class TestOneSentenceManySteps(PlannerBase):
    """Steps the customer said in one sentence are one request: the frequency limit doesn't stop step 4."""

    def setUp(self):
        super().setUp()
        limit = patch.object(settings, "velocity_max", 3)
        limit.start()
        self.addCleanup(limit.stop)

    def test_four_steps_in_one_sentence(self):
        self.use_plan(FOUR_STEPS)
        self.turn(SAID_FOUR)
        for _ in range(3):
            self.assertEqual(self.sign_all()["status"], "SUCCESS")
            self.next_step()
        d = self.s.current_draft
        self.assertEqual((d.payee_id, d.amount_cents), ("p_grandson_sam", 500))
        self.assertNotIn("R21_VELOCITY", d.risk.rule_hits)
        self.assertNotEqual(d.risk.level, RiskLevel.BLOCK)
        self.assertEqual(self.sign_all()["status"], "SUCCESS")

    def test_a_new_request_after_the_plan_still_hits_the_limit(self):
        self.use_plan(FOUR_STEPS)
        self.turn(SAID_FOUR)
        for _ in range(3):
            self.sign_all()
            self.next_step()
        self.sign_all()
        self.turn("Send 20 to my daughter")                       # not part of that sentence: counted as usual
        self.assertIn("R21_VELOCITY", self.s.current_draft.risk.rule_hits)

    def test_separate_requests_still_hit_the_limit(self):
        for amount in (10, 11, 12):
            self.turn(f"Send {amount} to my daughter")
            self.sign_all()
        r = self.turn("Send 13 to my daughter")
        self.assertIn("R21_VELOCITY", self.s.current_draft.risk.rule_hits)
        self.assertTrue(r.reply.startswith("I can't do this one. Too many payments"))

    def test_plan_id_is_signed(self):
        self.use_plan(FOUR_STEPS)
        self.turn(SAID_FOUR)
        d = self.s.current_draft
        self.assertTrue(d.plan_id.startswith("p_"))
        self.assertEqual(d.card_fields()["plan_id"], d.plan_id)


class TestThePlanCannotMoveMoney(PlannerBase):
    def test_reference_to_an_unsigned_step_asks(self):
        self.use_plan(HALF_THAT_MONEY)
        self.turn(SAID)
        r = self.next_step()                                            # step 1 was never signed
        self.assertIn("Which exact amount", r.reply)
        self.assertFalse([d for d in self.s.drafts.values() if d.payee_id == "p_daughter"])

    def test_a_reference_to_an_unverified_step_asks(self):
        self.use_plan(HALF_THAT_MONEY)
        self.kernel.gateway.simulator.settle_seconds = 3600             # PayPal hasn't confirmed step 1
        self.turn(SAID)
        self.sign_all()
        r = self.next_step()
        self.assertIn("Which exact amount", r.reply)

    def test_a_different_number_is_blocked(self):
        self.use_plan(HALF_THAT_MONEY)
        self.turn(SAID)
        self.sign_all()
        said = self.s.plan.pop(0)
        spec = self.s.plan_specs.pop(said)
        text, meta = self.agent._planned_step(self.s, 0, spec, self.s.plan_raw)
        self.s.messages.append({"role": "user", "content": text, "raw_content": self.s.plan_raw,
                                "ground_text": self.s.plan_raw, "plan_pos": 2, "plan_total": 2,
                                "resolved": meta["resolved"]})
        res = self.gw.call(self.s, "draft_transfer", {"payee_id": "p_daughter", "amount": 150})
        self.assertTrue(res["ok"], res)
        self.assertIn("R53_WRONG_SHARE", self.s.current_draft.risk.rule_hits)

    def test_a_step_the_user_never_asked_for_is_blocked(self):
        injected = [HALF_THAT_MONEY[0], {"said": "then give my daughter half that money", "request": "Send 900 to Ben Lee",
                                         "action": "transfer", "amount": {"kind": "fixed", "value": 900}}]
        self.use_plan(injected)
        self.turn(SAID)
        self.sign_all()
        self.next_step()
        d = self.s.current_draft
        self.assertEqual(d.risk.level, RiskLevel.BLOCK)                 # neither who nor how much was said

    def test_garbage_plan_falls_back_to_rules(self):
        self.use_plan([{"said": "x"}])
        r = self.turn("Send a grand to my son and then send half of it to my daughter")
        self.assertTrue(any("rule-based splitter used" in t["label"] for t in self.s.trace))
        self.assertEqual(r.ui["plan"], ["Send a grand to my son", "send half of it to my daughter"])

    def test_plain_one_step_request_is_not_planned(self):
        self.use_plan([{"said": "send 50 to my daughter", "request": "Send 50 to my daughter", "action": "transfer",
                     "amount": {"kind": "fixed", "value": 50}}])
        self.turn("Send 50 to my daughter and say hi")
        self.assertEqual(self.s.current_draft.amount_cents, 5000)
        self.assertFalse(self.s.plan_specs)


class TestVerifierStaysIndependent(PlannerBase):
    def test_model_verifier_reads_the_original_words_and_the_step_number(self):
        self.use_plan(HALF_THAT_MONEY)
        self.turn(SAID)
        utt, step = Verifier(self.stub, self.wallet)._transcript(self.s)
        self.assertEqual(utt, [SAID])
        self.assertEqual(step, {"current_step": 1, "steps_in_request": 2})


class TestValidate(PlannerBase):
    def test_closed_vocabulary(self):
        self.assertIsNone(planner.validate({"steps": []}))
        self.assertIsNone(planner.validate({"steps": [{"said": "a", "request": "b", "action": "hack"}]}))
        # a step can only refer to an EARLIER step
        bad = [dict(HALF_THAT_MONEY[1], amount={"kind": "share_of_step", "step": 2, "fraction": 0.5})]
        self.assertIsNone(planner.validate({"steps": bad}))
        ok = planner.validate({"steps": HALF_THAT_MONEY})
        self.assertEqual([s["pos"] for s in ok], [1, 2])
