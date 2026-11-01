# Design: invariants, payment lifecycle, decisions

How the money path is meant to behave, and why. For API detail see [PAYPAL_NOTES.md](PAYPAL_NOTES.md); for threats see [SECURITY.md](SECURITY.md).

## Invariants

| # | Invariant | Tests |
| --- | --- | --- |
| I1 | No module under `app/agent/` imports `app.paypal.client`. The agent only gets `PayPalReader`, whose transport allows named read calls, `search-invoices` and the token call, and raises for anything else before the socket | `tests/test_architecture.py` |
| I2 | No tool in the tool gateway can reach a money-moving call. Every tool is read, draft or control | `tests/test_architecture.py` |
| I3 | Every money-moving call has a `PaymentRecord` saved **before** the HTTP request | `tests/test_payment_flows.py`, `test_recovery.py` |
| I4 | Idempotency keys are pure functions of the signed digest | `tests/test_paypal_client.py` |
| I5 | "Paid" or "Sent" comes only from `verified=True` state, set by a signature-verified webhook or an authenticated GET. Redirects, query strings and browser messages never set it | `tests/test_payment_flows.py`, `test_webhooks.py` |
| I6 | An unknown outcome never produces a new attempt with a new key. A new draft for the same payee and amount is blocked while one is unknown (R60) | `tests/test_reconcile.py` |
| I7 | Fail closed: no credentials, an unknown mode or an unverifiable webhook means nothing executes, and the UI says why. Never a fallback to the simulator | `tests/test_modes.py`, `test_webhooks.py` |
| I8 | No secret in logs or the audit chain. Receiver emails are masked in the audit and ticker | `tests/test_paypal_client.py`, `test_webhooks.py` |
| I9 | A guardian approval can never unlock a BLOCK or a FROZEN draft, and the guardian can't start a payment | `tests/test_guardian.py` |
| I10 | No live base URL exists anywhere in the code. `PAYPAL_MODE=live` is refused | `tests/test_architecture.py`, `test_modes.py` |

## Payment lifecycle

`ExecutionGateway.execute` in `app/wallet/gateway.py` checks the digest, signed payload, enrolled credential, replay list, expiry, guardian approval and dependencies first. Then:

```
with lock:
    an existing record for this key?  return it                 # a double click returns the same record
    velocity, freeze, no unknown outcome for this payee
    reserve allowance
    save a record in SUBMITTING                                 # I3
HTTP outside the lock:
    transfer -> create_payout | purchase -> create_order | refund -> refund_capture or cancel_payout_item
    bill -> no write call: the payer's own invoice link | money_request -> create_invoice, send_invoice
with lock:
    apply the result, audit, flush
```

An unknown outcome isn't an error to the customer: the record becomes UNKNOWN, and she hears "I can't confirm that went through yet. Please don't pay again." A definite rejection means FAILED, and the allowance is released.

```
transfer:  SUBMITTING -> PROCESSING -> SENT | UNCLAIMED -> (SENT | RETURNED) | FAILED | HELD
purchase:  SUBMITTING -> AWAITING_APPROVAL -> APPROVED -> CAPTURING -> PAID | CAPTURE_PENDING -> PAID | DECLINED
           AWAITING_APPROVAL -> BUYER_CANCELLED | EXPIRED
bill:      AWAITING_PAYMENT -> PAID | INVOICE_CANCELLED
request:   SUBMITTING -> SENT_TO_PAYER -> PAID | INVOICE_CANCELLED
refund:    SUBMITTING -> REFUND_PENDING -> REFUNDED | FAILED
any write: SUBMITTING | CAPTURING -> UNKNOWN -> (resolved state);  any -> ANOMALY (terminal, needs a person)
```

Terminal success states can only be set with `verified=True` and evidence of the source.

### Reconciliation (`app/paypal/reconcile.py`)

| Unknown at | Query | Then |
| --- | --- | --- |
| Payout create | A local webhook, then an identical re-POST with the same `sender_batch_id` | Batch found: GET. Otherwise stays UNKNOWN |
| Order create | An identical POST with the same `PayPal-Request-Id` | Order found: continue |
| Capture | `GET` the order and look for captures | Found: verify. None and still approved: retry with the **same** request id |
| Refund | The same request id | Found: verify |
| Invoice create or send | `search-invoices` by invoice number | Found: carry on at the right step. Not found: create again |

An unknown outcome older than `PAYPAL_UNKNOWN_ALERT_MIN` alerts the guardian. Star never tells her to try again while one is unknown.

## Trust rules (`app/kernel/trust.py`)

| Rule | Stops or warns on | Level |
| --- | --- | --- |
| R80 | An invoice note with a phone number to call | BLOCK on Enhanced protection, otherwise HIGH |
| R81 | Urgent or frightening wording on an invoice | HIGH |
| R82 | A biller she has never paid and hasn't saved | HIGH |
| R83 | A bill over twice her usual one from that biller | MEDIUM |
| R84 | An instruction to the AI hidden in an invoice or a listing | BLOCK |
| R90 | A new merchant she has never bought from | HIGH, and the family checks |
| R91 | A price far above the usual price | BLOCK |
| R92 | A flagged merchant | BLOCK |
| R93 | An offer, seller or price that isn't exactly the catalogue's | BLOCK |
| R94 | Gift cards | BLOCK if she was told to buy them, otherwise HIGH, and the family checks |
| R95 | A refund of something that isn't hers, can't be reversed, or is over what's left | BLOCK |
| R96 | "We refunded you too much, send the difference" | BLOCK |

Base rules are in `app/kernel/rules.py`: unknown payee, allowance, limits, new payee, velocity, night, grounding (R50 to R53), unknown outcome (R60), recent duplicates (R61), a bill already being paid (R62) and the small-payment-first ramp on someone she added herself (R15).

## Decisions

| Topic | Decision | Why |
| --- | --- | --- |
| Identity | Star, with its own colours and shield. No PayPal logo; the word PayPal only as plain text naming the API | Must not look official |
| Currency | USD through one setting, `WALLET_CURRENCY` | Sandbox defaults, global judges |
| Whose account | One REST app on one sandbox business account: the household. It sends payouts, is merchant on orders, and issues invoices. Mrs. Lee is a personal sandbox account who approves orders and pays invoices | What client credentials allow. Said plainly, not hidden |
| Balance | A family-set allowance, not a PayPal balance | Not generally available through these APIs, and useful in itself |
| Approval | Full-page redirect to the approval link, no JS SDK | Keeps the CSP free of third-party scripts |
| Agent Toolkit, MCP | Not used | They give a model tools such as `pay_order`, the pattern Star rejects |
| Simulator | A `requests` adapter on the real client's session; a hostname that can't resolve | The real client always runs, and a bug can't reach a network |
| Speech | Browser recognition by default. Optional `ASR_PROVIDER=openai_compat` keeps signed-recording evidence | No vendor dependence |
| Replaces a card lock | A freeze: instant for Mrs. Lee or Anna, lifted only by both signatures | Protective actions should be fast, and undoing them slow |

**Not doing, on purpose:** live PayPal mode, Subscriptions, Vault, JS SDK v6, an MCP server, real email or SMS, purchases with no human approval, accepting or fighting disputes, onboarding merchants.
