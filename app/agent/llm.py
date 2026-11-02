"""Model clients.

* OpenAICompatLLM: any OpenAI-compatible /chat/completions API, with Function Calling.
* OfflineLLM: a rule-based "model" that needs no key and has exactly the same interface (it also returns tool_calls).
  It is NOT an AI model and the app says so. Uses: (1) run the whole pipeline before you have a key; (2) fallback when
  the online model times out or errors; (3) evaluation baseline.
"""
from __future__ import annotations

import json
import re
import time
import uuid

import requests

from app.agent import fraud
from app.agent.textparse import fix_asr, has_overlong_number, match_payees, parse_amount
from app.config import settings
from app.kernel.evidence import confirmed_proposal
from app.kernel.hotline import hotline_words
from app.models import fmt_money
from app.wallet.catalogue import known_item_words
from app.wallet.payments import EMAIL


class LLMError(Exception):
    pass


class OpenAICompatLLM:
    name = "openai_compat"
    supports_tool_choice = True      # "required" forces a tool call; switched off if the server rejects it

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30, thinking: str = ""):
        self.base_url, self.api_key, self.model, self.timeout = base_url.rstrip("/"), api_key, model, timeout
        self.thinking = thinking

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             model: str | None = None, tool_choice: str | None = None) -> dict:
        body = {"model": model or self.model, "messages": messages, "temperature": 0.1,
                "max_tokens": settings.llm_max_tokens}
        if self.thinking == "off":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice if (tool_choice and self.supports_tool_choice) else "auto"
        r = self._post(body)
        if r.status_code == 400 and body.get("tool_choice") == "required":
            self.supports_tool_choice = False           # this server can't force tool use; the harness covers it
            body["tool_choice"] = "auto"
            r = self._post(body)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            msg = r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError(f"Unexpected response: {r.text[:200]}") from e
        return {"role": "assistant", "content": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or []}

    def _post(self, body: dict):
        try:
            return requests.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                                 headers={"Authorization": f"Bearer {self.api_key}"})
        except requests.RequestException as e:
            raise LLMError(f"Network error: {e}") from e


# ====================================================================== Offline model
_RX = {
    "handoff": r"human|real person|customer service|(talk|speak) to (someone|a person|staff|an officer)",
    # Only an outright request freezes at once. A lost phone on its own makes Star ASK first (LOST_DEVICE, agent/loop.py).
    "freeze": r"\bfreeze\b|\b(stop|block|lock)\b.{0,20}\b(everything|all (my )?payments|my (wallet|account|paypal))\b",
    "stop_payout": r"\b(stop|cancel|take back|call back)\b.{0,25}\b(that|the|my)\s+(payment|payout|transfer)\b",
    "refund": r"\bmoney back\b|\brefund\b|\breturn (it|this|the)\b|\bsend (it|this) back\b",
    "money_request": r"\b(ask|request)\b.{0,40}\b(pay me|pay back|paying me|money|to pay)\b|\bowes? me\b|\bpay me back\b",
    "disputes": r"\bdisputes?\b|\b(paypal )?cases?\b|\bcomplaints?\b",
    "status": r"\b(go|gone|went) through\b|\bdid (it|that|my|the)\b.{0,25}\b(arrive|work|send|sent|pay|paid)\b|\bstatus\b|"
              r"\bwhere is my (payment|money|refund)\b",
    "invoice_ask": r"\bwhat('?s| is| are)\b.{0,25}\b(bill|bills|invoice|invoices)\b|\b(new|strange|funny|odd) (bill|invoice)\b|"
                   r"\bdo i (have|owe)\b.{0,25}\b(bill|bills|invoice)|\bexplain\b.{0,25}\b(bill|invoice)\b|\bthis (bill|invoice)\b.{0,20}\?",
    "buy": r"\b(buy|order|purchase|get me|i need a|i need some|shop for|pick up)\b",
    "bill": r"\bbills?\b|\binvoices?\b|\belectric|\bwater\b|\bpower\b|\butilit|securetech|riverside|maple county",
    "pay": r"\b(?:pay|settle)\b",
    "transfer": r"\b(transfer|send|wire|remit)\b|\bgive\b(?! me\b)",
    "limits": r"\blimits?\b",
    "add_payee": r"\b(add|register|save|new)\b.{0,25}\b(payee|person|contact)\b|\badd\b.{0,80}@",
    "income": r"pension|(come|came|gone) in|arrived|received",
    "balance": r"balance|allowance|how much (money )?(do i have|have i got|is (there )?in|is left|left|can i spend)",
    "spending": r"\bspen[dt]|spending|expenses|transactions|history",
}


# Her phone, wallet or purse is lost or stolen ("lost my phone", "my wallet was stolen"), not an accessory ("phone charger")
LOST_DEVICE = re.compile(
    r"\b(?:lost|lose|losing|misplaced|can'?t find|cannot find|stolen|stole|snatched|took)\b.{0,25}?"
    r"\b(?:phone|cell ?phone|mobile|device|tablet|ipad|wallet|purse|handbag)\b(?!\s*(?:charger|case|cover|cable|stand|holder|number|screen))|"
    r"\b(?:phone|cell ?phone|mobile|device|tablet|ipad|wallet|purse|handbag)\b.{0,30}\b(?:it'?s|it is|is|was|got|has been|been) "
    r"(?:stolen|lost|taken|gone|missing)\b", re.I)


def _has(key: str, text: str) -> bool:
    return bool(re.search(_RX[key], text, re.I))


def detect_intent(text: str) -> str:
    if re.search(r"\bhow much did I (?:send|pay|transfer)\b", text, re.I) and not re.search(r"\b(?:same again|usual amount)\b", text, re.I):
        return "spending"
    if _has("handoff", text):
        return "handoff"
    if _has("freeze", text):
        return "freeze"
    if _has("stop_payout", text):
        return "stop_payout"
    if _has("refund", text):
        return "refund"
    if _has("money_request", text):
        return "money_request"
    if _has("limits", text):
        return "limits"
    if _has("add_payee", text):
        return "add_payee"
    if _has("status", text):
        return "status"
    if _has("disputes", text):
        return "disputes"
    if _has("invoice_ask", text):
        return "bill_query"
    if _has("bill", text) and _has("pay", text):
        return "bill_pay"
    if _has("transfer", text) or (_has("pay", text) and not _has("bill", text)):
        return "transfer"
    if _has("buy", text) or any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text, re.I) for w in known_item_words()):
        return "purchase"
    if _has("bill", text):
        return "bill_query"
    if _has("income", text):
        return "income"
    if _has("balance", text):
        return "balance"
    if _has("spending", text):
        return "spending"
    return "unknown"


class OfflineLLM:
    """A deterministic stand-in: works in the shape of ReAct (Thought first, then Action) so the trace panel can show it.
    It has no intelligence; it reads known sentence shapes. Everything after it is the same code a real model goes through."""
    name = "offline"

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False,
             model: str | None = None) -> dict:
        if json_mode:   # scam assessment: use keyword rules when offline
            payload = json.loads(messages[-1]["content"])
            level, reasons, _ = fraud.scan(" ".join(payload.get("user_utterances", [])))
            return {"role": "assistant", "content": json.dumps({"level": level.value, "reasons": reasons},
                                                                ensure_ascii=False), "tool_calls": []}
        allowed = {t["function"]["name"] for t in tools or []}
        text = self._effective_text(messages)
        results = self._turn_results(messages)
        intent = detect_intent(text)

        # The previous tool errored: relay the actionable error to the user
        last = messages[-1]
        if last["role"] == "tool":
            data = json.loads(last["content"])
            if not data.get("ok"):
                return self._say(f"Sorry — {data['error']}")

        if intent == "handoff":
            if "handoff_to_human" not in results:
                return self._call("handoff_to_human", {"reason": "User asked for a human"},
                                  "The user wants a real person; hand off.")
            return self._say("This demo has no live staff service. Please talk to your family, or contact PayPal's customer service directly.")

        if intent == "freeze":
            if "freeze_payments" not in results:
                return self._call("freeze_payments", {"reason": "The user asked to freeze payments"},
                                  "Freeze now; it's a protective action.")
            return self._say("Done. All payments are frozen. Nothing can be sent until you and your family lift the freeze.")

        if intent == "limits":
            amt = parse_amount(text)
            if amt and re.search(r"\b(lower|reduce|decrease|cut|drop|set|change|raise|increase|higher|up|make)\b", text, re.I):
                if "set_daily_limit" not in results:
                    if "set_daily_limit" not in allowed:
                        return self._say("I can't change limits right now. Please finish what's on the card first.")
                    return self._call("set_daily_limit", {"amount": amt, "evidence": self._last_user(messages)},
                                      "The user wants a different daily limit; lowering is instant, raising "
                                      "needs their signature.")
                if "change_id" in results["set_daily_limit"]["data"]:
                    return self._say("A higher limit needs your fingerprint, and for your safety it only starts after "
                                     "a waiting time. Please check the card.")
                return self._say(f"Done. Your daily limit is now ${amt:,.0f}, starting right away.")
            if "get_limits" not in results:
                return self._call("get_limits", {}, "The user asks about their limit.")
            d = results["get_limits"]["data"]
            return self._say(f"Your daily limit for money to other people is {d['daily_transfer_limit']}. "
                             f"You've used {d['used_today']} today, so {d['left_today']} is left.")

        if intent == "add_payee":
            if "request_add_payee" in results:
                return self._say("Please check the card and every letter of the email. If it's right, confirm with your fingerprint.")
            raw = " ".join(m["content"] for m in messages if m["role"] == "user")[-400:]
            email = EMAIL.search(raw)
            name_m = re.search(r"\b(?:person|payee|contact)[:,]?\s+(?:called |named )?([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,3})", raw) or \
                re.search(r"\badd\s+(?:my \w+\s+)?([A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,3})", raw)
            name = name_m.group(1) if name_m else ""
            if not (email and name):
                return self._say("To add a new person I need their name and their PayPal email. What are they?")
            if "request_add_payee" not in allowed:
                return self._say("For your safety I can't add anyone in this conversation.")
            rel = re.search(r"\bmy (friend|neighbou?r|niece|nephew|cousin|sister|brother|helper|doctor)\b", raw, re.I)
            return self._call("request_add_payee", {"name": name, "email": email.group(0).lower(),
                                                    "relation": rel.group(1).lower() if rel else "",
                                                    "evidence": self._last_user(messages)},
                              "Ask to add the person the user described; they must sign it.")

        if intent == "balance":
            if "get_allowance" not in results:
                return self._call("get_allowance", {}, "The user wants to know what's left; look up the allowance.")
            a = results["get_allowance"]["data"]
            return self._say(f"You have {a['left']} left of your {a['monthly_allowance']} Star allowance. "
                             "That's the limit you and your family set. I can't see your PayPal balance.")

        if intent in ("income", "spending"):
            if "get_recent_transactions" not in results:
                return self._call("get_recent_transactions", {"limit": 8 if intent == "income" else 20}, "Need to see recent transactions.")
            txns = results["get_recent_transactions"]["data"]
            if intent == "income":
                inc = next((t for t in txns if t["amount"].startswith("+")), None)
                if not inc:
                    return self._say("I don't see any recent incoming payment.")
                return self._say(f"Yes — {inc['desc']} of {inc['amount'][1:]} arrived on {inc['date']}.")
            month = self._this_month(messages)        # her month, in her own time zone, never the data's
            outs = [t for t in txns if t["amount"].startswith("-") and t["date"].startswith(month)]
            total = sum(-t["amount_cents"] for t in outs)
            named = time.strftime("%B %Y", time.strptime(month, "%Y-%m"))
            if not outs:
                return self._say(f"You haven't spent anything yet this month ({named}).")
            return self._say(f"This month ({named}) you've spent {fmt_money(total)} across {len(outs)} payment{'' if len(outs) == 1 else 's'} so far.")

        if intent == "status":
            if "payment_status" not in results:
                return self._call("payment_status", {}, "Look up what PayPal has verified.")
            rows = results["payment_status"]["data"]
            if not rows:
                return self._say("You haven't made any payments with me yet.")
            r = rows[0]
            word = {"SENT": "was received", "PAID": "is paid", "REFUNDED": "was refunded"}.get(r["state"])
            if word and r["verified"]:
                return self._say(f"Yes. {r.get('confirmed_by', 'PayPal')} confirms your {r['amount']} for {r['what']} {word}.")
            if r["state"] == "UNKNOWN":
                return self._say(f"I can't confirm your {r['amount']} for {r['what']} yet. Please don't pay again. I'm checking with PayPal.")
            return self._say(f"Your {r['amount']} for {r['what']} is not finished yet: {r['state'].replace('_', ' ').lower()}.")

        if intent == "disputes":
            if "list_disputes" not in results:
                return self._call("list_disputes", {}, "Read the PayPal cases.")
            d = results["list_disputes"]["data"]
            if not d.get("available"):
                return self._say("I can't read your PayPal cases right now.")
            if not d["cases"]:
                return self._say("You have no open PayPal cases.")
            return self._say(f"You have {len(d['cases'])} PayPal case{'s' if len(d['cases']) > 1 else ''}: "
                             + "; ".join(f"{c['reason'].replace('_', ' ').lower()}, {c['state'].replace('_', ' ').lower()}" for c in d["cases"]) + ".")

        if intent == "bill_query":
            return self._invoice_query(text, results)

        if intent == "bill_pay":
            if "list_invoices" not in results:
                return self._call("list_invoices", {}, "First check which invoices are unpaid.")
            bills = results["list_invoices"]["data"]
            if not bills:
                return self._say("You have no unpaid bills right now.")
            picked = self._pick_bill(text, bills)
            if picked is None:
                lst = ", ".join(b["title"] for b in bills)
                return self._say(f"You have these bills: {lst}. Which one would you like to pay?")
            if "draft_bill_payment" not in results:
                if "draft_bill_payment" not in allowed:
                    return self._say("You still have a payment waiting for confirmation. Please confirm or cancel it first.")
                return self._call("draft_bill_payment", {"invoice_id": picked["invoice_id"], "evidence": self._last_user(messages)},
                                  f"Found the invoice {picked['title']}; draft a payment for the user to confirm.")
            return self._draft_done(results["draft_bill_payment"])

        if intent == "purchase":
            return self._purchase(text, results, messages, allowed)

        if intent == "refund":
            return self._refund(text, results, messages, allowed, "draft_refund", "refund")

        if intent == "stop_payout":
            return self._refund(text, results, messages, allowed, "draft_stop_payout", "stop")

        if intent == "money_request":
            if "list_payees" not in results:
                return self._call("list_payees", {}, "Who should pay her back?")
            people = [p for p in results["list_payees"]["data"] if p["type"] == "person"]
            hits = match_payees(text, people)
            if len(hits) != 1:
                opts = " or ".join(p["name"] for p in (hits or people))
                return self._say(f"Who should I ask: {opts}?")
            amount, _ = self._amount(text, messages)
            if amount is None:
                return self._say(f"How much should I ask {hits[0]['name']} for?")
            if "draft_money_request" not in results:
                if "draft_money_request" not in allowed:
                    return self._say("You still have something waiting for confirmation. Please confirm or cancel it first.")
                reason = re.search(r"\bfor (the |my )?([a-z ]{3,40})$", text, re.I)
                return self._call("draft_money_request", {"payee_id": hits[0]["payee_id"], "amount": amount, "evidence": self._last_user(messages),
                                                          "reason": reason.group(2) if reason else ""},
                                  f"Ask {hits[0]['name']} for {amount:g}; she signs it because it goes out in her name.")
            return self._draft_done(results["draft_money_request"])

        if intent == "transfer":
            raw_email = EMAIL.search(text)
            if "list_payees" not in results:
                return self._call("list_payees", {}, "The user wants to send money; first get the saved people "
                                                     "to see who they mean.")
            payees = [p for p in results["list_payees"]["data"] if p["type"] == "person"]
            hits = match_payees(text, payees)
            if not hits:
                if raw_email:
                    return self._say("For your safety I can only send money to people you've saved. Please add them "
                                     "first. If someone asked you to send money to an unfamiliar account, please talk "
                                     "to your family first.")
                names = ", ".join(f"your {p['relation']} {p['name']}" for p in payees)
                return self._say(f"Your saved people are {names}. Who would you like to send money to?")
            if len(hits) > 1:
                rel = hits[0]["relation"] if len({p["relation"] for p in hits}) == 1 else "people"
                opts = " or ".join(f"{p['name']} ({p['account']})" for p in hits)
                return self._say(f"You have {len(hits)} {rel}s saved. Do you mean {opts}?" if rel != "people"
                                 else f"Which one do you mean: {opts}?")
            payee = hits[0]
            amount, slip = self._amount(text, messages)
            if slip and not slip["sure"]:
                return self._say(f"Did you mean {slip['meant']}, {fmt_money(round(amount * 100))}?")
            if amount is None and re.search(r"\b(?:usual amount|same again)\b", text, re.I):
                if "get_recent_transactions" not in results:
                    return self._call("get_recent_transactions", {}, "Read the previous payment before reusing its amount.")
                previous = next((t for t in results["get_recent_transactions"]["data"]
                                 if t.get("payee_id") == payee["payee_id"] and t.get("amount_cents", 0) < 0), None)
                if previous:
                    amount = -previous["amount_cents"] / 100
            if amount is None:
                if has_overlong_number(text):
                    return self._say(f"That number is too long to be an amount, so I won't use it. How much would you like to send to {payee['name']}?")
                return self._say(f"How much would you like to send to your {payee['relation']} {payee['name']}?")
            if "draft_transfer" not in results:
                if "draft_transfer" not in allowed:
                    return self._say("For your safety I can't set up a payment in this conversation. If someone "
                                     "claiming to be the police or a company asked you to send money, please hang up "
                                     f"and call {hotline_words()}.")
                args = {"payee_id": payee["payee_id"], "amount": amount, "evidence": self._last_user(messages)}
                if slip:
                    args.update(misheard=slip["heard"], meant=slip["meant"])
                return self._call("draft_transfer", args, f"\"{payee['relation']}\" is {payee['name']} ({payee['payee_id']}), "
                                                          f"amount {amount:g}; draft the transfer.")
            return self._draft_done(results["draft_transfer"])

        return self._say("I can send money to your family, buy what you need from sellers I've checked, pay your bills, get "
                         "your money back, or ask someone to pay you. What would you like to do?")

    # ------------------------------------------------------------------ helpers
    def _invoice_query(self, text: str, results: dict) -> dict:
        if "list_invoices" not in results:
            return self._call("list_invoices", {}, "See which invoices have arrived.")
        bills = results["list_invoices"]["data"]
        if not bills:
            return self._say("You have no unpaid bills right now.")
        wrong = [b for b in bills if b["looks_wrong"]]
        named = self._pick_bill(text, bills)
        target = named or (wrong[0] if wrong and re.search(r"\b(this|that|new|strange|funny|odd|the)\s+(bill|invoice)\b", text, re.I) else None)
        if target and "explain_invoice" not in results:
            return self._call("explain_invoice", {"invoice_id": target["invoice_id"]}, "Check this invoice before saying anything about it.")
        if "explain_invoice" in results:
            d = results["explain_invoice"]["data"]
            if d["warnings"]:
                advice = "Please don't pay it or call any number in it." if not d["can_pay"] else "Your family will need to agree before this can be paid."
                return self._say(f"Please be careful. This bill from {d['from']} for {d['amount']} looks wrong. "
                                 + " ".join(d["warnings"][:3]) + f" {advice}")
            return self._say(f"This is your {d['title']} from {d['from']}: {d['amount']}, due {d['due']}. It looks normal. Shall I prepare it for you to pay?")
        lst = "; ".join(f"{b['title']} from {b['from']}, {b['amount']}" + (" (this one looks wrong)" if b["looks_wrong"] else "") for b in bills)
        return self._say(f"You have {len(bills)} unpaid bill{'s' if len(bills) > 1 else ''}: {lst}.")

    def _purchase(self, text: str, results: dict, messages: list[dict], allowed: set) -> dict:
        if "search_catalogue" not in results:
            return self._call("search_catalogue", {"query": text[:120]}, "Look up offers and let Star's checks grade them.")
        data = results["search_catalogue"]["data"]
        offers = data["offers"]
        if not offers:
            return self._say("I can't find that from the sellers I've checked. I can find a blood pressure monitor, a weekly groceries box or a gift card.")
        named = [o for o in offers if o["merchant"].lower() in text.lower()]
        pick = named[0] if named else next((o for o in offers if o["verdict"] != "stop"), None)
        if pick is None:
            return self._say("I stopped this: " + " ".join(offers[0]["reasons"][:2]))
        if "draft_purchase" not in results:
            if "draft_purchase" not in allowed:
                return self._say("For your safety I can't buy anything in this conversation. Please talk to your family.")
            args = {"offer_id": pick["offer_id"], "evidence": self._last_user(messages)}
            many = re.search(r"\b(two|three|four|five|[2-9])\s+(?:of\s+)?(?:blood|box|boxes|bp|monitors?|grocer)", text, re.I)
            if many:
                args["quantity"] = {"two": 2, "three": 3, "four": 4, "five": 5}.get(many[1].lower()) or int(many[1])
            return self._call("draft_purchase", args, f"Offer {pick['offer_id']} from {pick['merchant']} is the one the checks like best.")
        d = results["draft_purchase"]["data"]
        stopped = [o for o in offers if o["verdict"] == "stop" and o["offer_id"] != pick["offer_id"]]
        done = self._draft_done(results["draft_purchase"])
        if stopped and not d.get("frozen") and d["risk_level"] != "BLOCK":
            done["content"] = f"I found {len(offers)} offers and stopped {stopped[0]['merchant']}: {stopped[0]['reasons'][0]} " + done["content"]
        return done

    def _refund(self, text: str, results: dict, messages: list[dict], allowed: set, tool: str, how: str) -> dict:
        if "list_refundable" not in results:
            return self._call("list_refundable", {}, "See which verified payments can come back.")
        rows = [r for r in results["list_refundable"]["data"] if (r["how"] == "refund") == (how == "refund")]
        if not rows:
            return self._say("I don't see a payment I can get back right now." if how == "refund"
                             else "I don't see a payment that can still be stopped. Once someone has received it, it can't be stopped.")
        low = text.lower()
        hits = [r for r in rows if r["what"].lower() in low or r["to"].lower() in low]
        if not hits and len(rows) == 1:
            hits = rows
        if len(hits) != 1:
            return self._say("Which one do you mean: " + " or ".join(f"{r['what']} ({r['amount']})" for r in (hits or rows)) + "?")
        if tool not in results:
            if tool not in allowed:
                return self._say("Please confirm or cancel what's on the card first.")
            return self._call(tool, {"payment_id": hits[0]["payment_id"], "evidence": self._last_user(messages)},
                              f"Draft getting back {hits[0]['what']}; she signs it.")
        return self._draft_done(results[tool])

    @staticmethod
    def _draft_done(res: dict) -> dict:
        d = res["data"]
        if d.get("frozen"):
            return OfflineLLM._say("I've stopped this one: " + " ".join(d.get("mismatches", [])))
        if d["risk_level"] == "BLOCK":
            why = d["reasons"][0] if d["reasons"] else ""
            return OfflineLLM._say(f"I can't pay this one. {why}")
        verb = {"refund": "back from", "money_request": "asked from", "purchase": "to"}.get(d.get("kind"), "to")
        warn = f"{d['warning']} " if d.get("warning") else ""
        if d.get("family") == "unpaired":
            return OfflineLLM._say(f"{warn}This one needs your family's OK: {d['amount']} {verb} {d['payee']}. Your family guardian isn't set up yet, "
                                   "so it can't be sent. You can invite them on the Family page, or cancel this.")
        if d.get("family") == "waiting":
            return OfflineLLM._say(f"{warn}This one needs your family's OK: {d['amount']} {verb} {d['payee']}. {d['guardian']} sees this exact card on her own "
                                   "device. Once she approves, you can sign it.")
        return OfflineLLM._say(f"Please check the card: {d['amount']} {verb} {d['payee']}. If it's right, confirm with your fingerprint.")

    @staticmethod
    def _pick_bill(text: str, bills: list[dict]) -> dict | None:
        rules = [(r"electric|\bpower\b|riverside|utilit", "p_power"), (r"\bwater\b|maple county", "p_water")]
        for rx, pid in rules:
            if re.search(rx, text, re.I):
                b = next((b for b in bills if b["payee_id"] == pid), None)
                if b:
                    return b
        by_name = next((b for b in bills if b["from"].lower() in text.lower() or b["title"].lower() in text.lower()
                        or any(w in text.lower() for w in b["from"].lower().split() if len(w) > 5)), None)
        return by_name or (bills[0] if len(bills) == 1 else None)

    @staticmethod
    def _effective_text(messages: list[dict]) -> str:
        """Multi-turn slot filling: while the user is answering the assistant's questions ("Ben Lee", then
        "fifty dollars"), read those answers together with the request that started it ("Send money to my family")."""
        users = [i for i, m in enumerate(messages) if m["role"] == "user"]
        if not users:
            return ""
        parts = [messages[users[-1]]["content"]]
        k = len(users) - 1
        if k >= 1 and re.search(r"\b(?:her|him|them)\b.{0,20}\b(?:same|again)\b", parts[0], re.I):
            parts.insert(0, messages[users[k - 1]]["content"])
        while detect_intent(" ".join(parts)) == "unknown" and k >= 1 and len(parts) < 4:
            prev_asst = next((m for m in reversed(messages[users[k - 1]:users[k]]) if m["role"] == "assistant"
                              and m.get("content")), None)
            if not (prev_asst and re.search(r"\?\s*$", prev_asst["content"])):
                break
            k -= 1
            parts.insert(0, messages[users[k]]["content"])
        return " ".join(parts)

    @staticmethod
    def _turn_results(messages: list[dict]) -> dict:
        """Results of each tool in this turn (after the last user message)."""
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        names, out = {}, {}
        for m in messages[last_user:]:
            for tc in m.get("tool_calls") or []:
                names[tc["id"]] = tc["function"]["name"]
            if m["role"] == "tool":
                out[names.get(m["tool_call_id"], "?")] = json.loads(m["content"])
        return out

    def _amount(self, text: str, messages: list[dict]) -> tuple[float | None, dict | None]:
        """The amount, and the speech slip it came from if any. A model reads slips by itself; this planner uses
        textparse.fix_asr as a stand-in. "Yes" to our own "Did you mean a grand, $1,000?" gives that amount."""
        amount = parse_amount(text) or self._worked_out_amount(messages)
        if amount:
            return amount, None
        last = self._last_user(messages)
        last_i = max(i for i, m in enumerate(messages) if m["role"] == "user")
        prev = next((m["content"] for m in reversed(messages[:last_i]) if m["role"] == "assistant"
                     and m.get("content") and not m.get("tool_calls")), "")
        asked = bool(re.search(r"\bhow much\b", prev, re.I))
        for t in (last, text):
            fixed, slips = fix_asr(t, asked_amount=asked and t is last)
            if slips and parse_amount(fixed):
                return parse_amount(fixed), slips[0]
        proposal = confirmed_proposal(messages)
        return (parse_amount(proposal) if proposal else None), None

    @staticmethod
    def _this_month(messages: list[dict]) -> str:
        """'2026-11' from the state line "Today for the customer: 2026-11-01" (her time zone); the server's month if absent."""
        sysmsg = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        m = re.search(r"Today for the customer: (\d{4}-\d{2})-\d{2}", sysmsg)
        return m.group(1) if m else time.strftime("%Y-%m")

    @staticmethod
    def _worked_out_amount(messages: list[dict]) -> float | None:
        """'Use amount 812.50 exactly' from the session state (a relative amount the code worked out)."""
        sysmsg = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        m = re.search(r"Use amount (\d+(?:\.\d+)?) exactly", sysmsg)
        return float(m.group(1)) if m else None

    @staticmethod
    def _last_user(messages: list[dict]) -> str:
        return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")

    @staticmethod
    def _call(name: str, args: dict, thought: str) -> dict:
        return {"role": "assistant", "content": thought, "tool_calls": [{
            "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}

    @staticmethod
    def _say(text: str) -> dict:
        return {"role": "assistant", "content": text, "tool_calls": []}


def engine_label(llm) -> str:
    """What the UI says. The offline engine is named as what it is."""
    return "Offline rule engine (no AI model connected)" if str(getattr(llm, "name", "")).startswith("offline") \
        else f"AI model: {getattr(llm, 'model', getattr(llm, 'name', '?'))}"


def build_llm():
    if settings.llm_enabled:
        return OpenAICompatLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout,
                               settings.llm_thinking)
    return OfflineLLM()
