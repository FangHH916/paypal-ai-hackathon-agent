# Star on PayPal: engineering plan

Working document for the Backend, Product and Materials stages. Target: PayPal AI Hackathon, submission deadline
Nov 12 2026 12:00 PT. It names the old identity in order to remove it, so the Materials stage deletes or rewrites this
file before the repo goes public (the final grep in section 15 must come back clean).

Status words used below:

- **Spec-checked**: read in PayPal's official OpenAPI files (`github.com/paypal/paypal-rest-api-specifications`,
  Apache-2.0) or official docs pages during planning.
- **Unverified**: not confirmed by any document or call. Needs a code comment and a line in `docs/PAYPAL_NOTES.md`.
- No real sandbox call has been made. Nothing may claim otherwise until `scripts/paypal_sandbox_check.py` has really
  passed with real keys.

---

## 1. Product definition

**Star is a voice payment agent for seniors and their families, built on the PayPal sandbox.**
*"An agent that can really pay, and can't be talked into a scam."*

Mrs. Lee, 74, says what she wants in her own words. Star sends money to family (Payouts), buys what she asks for from
merchants it checked (Orders), handles her bills, which arrive as invoices (Invoicing), and gets her money back (Refund,
payout cancel, Disputes read). Her daughter Anna is her **family guardian**: she sees the activity and co-approves
risky payments from her own device.

**Core principle, unchanged:** the model understands, code decides, the customer authorises. The model has no tool that
moves money. Every money-moving PayPal call is reachable only through deterministic kernel, then device-signed card,
then zero-trust gateway.

**Who it is for:** older adults who already find payment apps hard and are targeted by scammers, and the adult children
who worry about them. PayPal's own inspiration list names seniors, families and accessibility as underserved.

**How it differs from existing work:** a typical payment agent gives the model `create_order` and `pay_order` tools and
asks it to behave. Star assumes the model will be fooled. It separates *understanding* from *authority*, so a fooled
model can produce nothing worse than a draft that gets frozen.

### The three moments a judge should remember

1. **"It really pays."** One spoken sentence: "Get me a blood pressure monitor." Star finds three offers, rejects a
   new merchant charging almost four times the usual price, puts the sensible one on a large-print card, she signs with
   her fingerprint, approves on PayPal, and the status changes to Paid only when the verified state arrives. The live
   PayPal call ticker shows each real request.
2. **"The invoice that talked back."** A fake invoice arrives: $499.99 "security renewal", with a call-this-number note
   and a hidden line telling the AI to approve it. Star reads it to her as a warning, the injected sentence is removed,
   code blocks it, Anna gets an alert, and the ticker reads **"PayPal money-moving calls: 0"**. A side panel replays the
   same invoice through a clearly labelled scripted naive agent that holds a raw pay tool, which pays.
3. **"Anna's phone lights up."** Split screen. Mrs. Lee asks to pay a new person $600. The card will not sign. On a second
   device Anna sees the exact card, and her own device signs approve or deny over the same digest. Deny: payment dead,
   nothing sent. Approve: Mrs. Lee can sign. The scammer now has to fool her, the code and her daughter.

Supporting beats, 10 to 15 seconds each: "Simulate a model mistake" (frozen, $500 for "send 50"); "Failure drill" (the
network dies mid-payout, double click, exactly one payout exists); "Stop! I want my money back" (refund by voice).

### Mapping to judging (equal weights)

| Criterion | What earns it |
| --- | --- |
| Technological Implementation | Five PayPal API families used in depth, webhook signature verification, write-ahead idempotency, reconciliation, contract tests checked against the official OpenAPI spec, plus model + verifier + scam judge |
| Design | A complete family product: Care mode, read-aloud, wallet home, one coherent identity, guardian view, activity grid |
| Potential Impact | FBI IC3 figures (section 13), a named audience, and each demonstrated flow answering a named scam type |
| Innovation | Authority separated from intelligence; two-person, digest-bound co-approval; PayPal idempotency keys derived from the signed card, offline checkable |
| Presentation | Three moments above, under 3 minutes, every simulated thing labelled |

Prize targets: Grand, Best Use of Agentic Commerce, Best Use of PayPal + AI, Most Impactful, Best Demo Delivery. Stack:
AG Grid and Render.

---

## 2. Decisions on open choices

| # | Topic | Decision | Why |
| --- | --- | --- | --- |
| D1 | Name, look | Keep "Star". Original identity: deep green `#0F5C4D`, warm amber `#F2A93B`, off-white; large type. Line in footer and README: "Built on the PayPal sandbox; not affiliated with PayPal." No PayPal logo; the word PayPal only as plain text naming the API | Avoid looking official |
| D2 | Currency | USD via one setting, `WALLET_CURRENCY=USD`. `fmt_money` and all copy read it | Global judges; the sandbox defaults fit |
| D3 | Persona | Mrs. Lee (Margaret Lee). Daughter Anna Lee (guardian), son David Lee, grandsons Ben Lee and Sam Lee (the "which grandson?" question stays). Timezone setting `CUSTOMER_TIMEZONE`, default `America/New_York` | Generic, keeps the disambiguation demo |
| D4 | Hotline | `SCAM_HOTLINE_COUNTRY=US` default. US: National Elder Fraud Hotline 833-FRAUD-11 (833-372-8311), checked on the DOJ OVC page. SG: ScamShield 1799, kept as a config value only and labelled "not re-verified" until someone checks it | Configurable, per brief |
| D5 | Whose PayPal account | One REST app on one **sandbox business account**, the "Household" account. It sends Payouts, is the merchant of record on Orders, and issues Invoices. Mrs. Lee is a **sandbox personal account** who approves orders and pays invoices. See section 4.1 and risk R3 | Client-credentials reality; said plainly in the docs, not hidden |
| D6 | Balance | PayPal balance is not used. Star enforces a **spending allowance** set with the family (default $1,500), held locally. Rule R03 becomes "over your Star allowance". UI says: "Star can't see your PayPal balance. This is the limit you and Anna set." | Honest, and a useful product feature |
| D7 | Approval UX | Full-page redirect to the approval link. No JS SDK | Keeps CSP free of third-party scripts; the spec documents redirect to `rel:approve` |
| D8 | Agent Toolkit / MCP | Not used at runtime. `paypal-agent-toolkit` hands a model tools such as `pay_order`, which is the exact pattern Star rejects. README explains this choice without criticising the toolkit, which is a reasonable tool for other uses | Stays out of the money path, adds no value on reads |
| D9 | Simulator | `requests` transport adapter mounted on the same `requests.Session` as the real client, base URL `https://paypal-simulator.invalid`. The client code is identical in both modes. Hostname cannot resolve, so a bug can't reach a network | Real client code always exercised |
| D10 | Speech | Remove the speech vendor ASR code. Default: browser speech recognition, as now. Optional `ASR_PROVIDER=openai_compat` calling `POST {base}/audio/transcriptions`, which keeps the signed-recording feature. Without it, no recordings exist and the UI says so | Removes old vendor, keeps the feature optional |
| D11 | LLM default | `LLM_BASE_URL` has no vendor default. `LLM_PROVIDER=offline` remains the keyless fallback and the UI **names it**: "Offline rule engine (no AI model connected)". See risk R1 | Honesty |
| D12 | Cut | Investments, GIRO, standing instructions, own-account transfers, multiple accounts, cards and lock card, risk profile and suitability, holdings | Don't belong in a PayPal wallet. Details in section 5 |
| D13 | Replaces lock card | `freeze_payments` control tool and a big "Freeze everything" button. Instant, for Mrs. Lee or Anna. Lifting needs Mrs. Lee's signature plus Anna's | Keeps the protective-action pattern |
| D14 | `kind` values | `transfer` stays (rail: Payouts) to limit churn. `bill` stays (rail: invoice). Add `purchase`, `refund`, `money_request` | Smaller diff, kernel tests survive |
| D15 | `from_account` | Stays on the card, always `WALLET` | Less churn in the signed payload and gateway |
| D16 | Compound requests | Stay: "pay the power bill, then send 200 to my daughter", "half of that". Account references ("half my savings") go | Planner still has a job and it demos well |
| D17 | Repo licence | MIT at repo top, with `THIRD_PARTY_NOTICES.md` (AG Grid MIT; OpenAPI fixtures Apache-2.0). Needs teammates' agreement, see section 16 | Rule requires a detectable licence |
| D18 | Provenance | README states: Star began as an Oct 2026 voice banking prototype for another hackathon, and lists what was rebuilt for this entry. Git log dates: all 54 prior commits are Oct 11 to 15 2026 | Rule requires an explanation |

**Not doing, on purpose:** live PayPal mode; Subscriptions; Vault; JS SDK v6; MCP server; real email or SMS (guardian
alerts are in-app, labelled); autonomous purchases with no human approval; accepting or fighting disputes (read only);
multi-merchant onboarding.

---

## 3. Architecture and invariants

```
 voice/text -> scam pre-screen -> AGENT (untrusted)            tools: read + draft + protective only
                                    | draft
                                    v
                                 KERNEL  grounding, verifier diff, rules, trust checks, guardian requirement
                                    | card (code-built) + digest
                                    v
                    device signature (passkey or browser key)  +  guardian signature when required
                                    v
                                 GATEWAY  re-verifies everything; write-ahead record; idempotency key from digest
                                    v
                                 app/paypal/client.py  (full client: gateway only)
                                    v
                        PayPal sandbox  |  simulator adapter
                                    v
       webhook (verified) or authenticated GET -> payment state -> UI, audit chain, receipt
```

Invariants. Each has a test (section 12).

- **I1** No module under `app/agent/` imports `app.paypal.client`. The agent only gets `PayPalReader`, whose session has
  a transport allowlist: GETs on named read paths, `POST /v2/invoicing/search-invoices`, and the token call. Anything
  else raises before the socket.
- **I2** No tool in the tool gateway can reach a money-moving call.
- **I3** Every money-moving call has a write-ahead `PaymentRecord` saved **before** the HTTP request.
- **I4** Idempotency keys are pure functions of the signed digest.
- **I5** "Paid" or "Sent" is shown only from `verified=True` state, set by a signature-verified webhook or an
  authenticated GET. Redirects, query strings and browser messages never set it.
- **I6** An unknown outcome never produces a new money-moving attempt with a new key, and a new draft for the same
  payee and amount is blocked while one is unknown (rule R60).
- **I7** Fail closed: no credentials, unknown mode, or unverifiable webhook means nothing executes and the UI says why.
  Never a fallback to the simulator.
- **I8** No secret in logs or the audit chain. Authorization headers and token bodies are never recorded; receiver emails
  are masked in the audit and ticker.
- **I9** Guardian approval can never unlock a BLOCK or a FROZEN draft, and the guardian cannot initiate payments.
- **I10** No live base URL exists anywhere in the code. `PAYPAL_MODE=live` is refused with a readable error.

---

## 4. PayPal integration specification

Source of truth: official OpenAPI files. Backend must re-read the named schema before coding each call. Only send
fields the spec defines. Error codes in code must be ones seen in the docs: `ORDER_NOT_APPROVED`,
`ORDER_ALREADY_CAPTURED` and `INSTRUMENT_DECLINED` were confirmed on the official Orders errors page; treat everything
else generically by HTTP status.

### 4.1 Roles in the sandbox (say this in the docs)

| Role | Sandbox account | Does |
| --- | --- | --- |
| Household + demo merchants | Business (owns the REST app) | Payouts sender; Orders merchant; Invoice issuer; Refund caller |
| Mrs. Lee | Personal | Approves orders, pays invoices |
| Family | Personal accounts, emails in `PAYPAL_SANDBOX_RECEIVERS` JSON keyed by payee id | Receive payouts |

Stated limitation: a production version would need each customer's own account connected through partner permissions,
which this entry does not build. All demo merchants settle into the one sandbox business account; the merchant names and
trust data are demo content.

### 4.2 Client basics (`app/paypal/client.py`)

- Base URL: sandbox `https://api-m.sandbox.paypal.com`. Simulator `https://paypal-simulator.invalid`.
- Token: `POST /v1/oauth2/token`, basic auth, `grant_type=client_credentials`. Cached under a lock until
  `expires_in - 60`. On a 401: drop the cache and repeat once, same idempotency key.
- Timeouts: connect 5 s, read 20 s (`PAYPAL_TIMEOUT`). **No automatic retry** on any write except the 401 case above.
- Every call produces a `CallRecord` (method, path template, status, duration, debug id, mode) going to the ticker and
  to the audit chain as `paypal.call`.
- Errors map to `PayPalError(status, name, debug_id, links)` or `PayPalUnknown` (timeout, connection reset, 5xx on a write).

### 4.3 Payouts (kind `transfer`)

- `POST /v1/payments/payouts`, spec-checked. Body: `sender_batch_header {sender_batch_id, email_subject, email_message}`,
  `items[{recipient_type:"EMAIL", amount:{currency,value}, receiver, note, sender_item_id}]`.
- `sender_batch_id = "star-" + digest[:40]`, also sent as `PayPal-Request-Id` (spec: optional, stored 30 days).
  `sender_item_id = digest[:32]`, limit 63.
- Status: `GET /v1/payments/payouts/{id}`, item: `GET /v1/payments/payouts-item/{id}`.
- Batch enum (spec): DENIED, PENDING, PROCESSING, SUCCESS, CANCELED.
  Item enum (spec): SUCCESS, FAILED, PENDING, UNCLAIMED, RETURNED, ONHOLD, BLOCKED, REFUNDED, REVERSED.
- **A batch SUCCESS doesn't mean the money arrived** (spec says to check each item). The status shown comes from the item.
  UNCLAIMED shows as "Waiting for Anna to claim it. You can still stop this."
- **Reconciliation.** No endpoint looks up a batch by `sender_batch_id`. The spec's documented mechanism: PayPal rejects
  a `sender_batch_id` used in the last 30 days with an error that includes a HATEOAS link to the original, and a 5xx
  may be safely retried with the same id. So: (1) check local verified webhook state; (2) re-POST the **identical body and
  key**, which acts as the query; (3) on 201, or on an error with a link to the original, GET that batch; (4) otherwise
  remain UNKNOWN, tell the user, never mint a new key. The error's exact JSON shape is **unverified**: parse `links[]`
  generically and have the sandbox check script print the real response.
- Stop: `POST /v1/payments/payouts-item/{id}/cancel`, spec-checked, only for UNCLAIMED. Voice: "stop that payment". Done
  as a signed `refund`-kind card, since it's a money movement. Result expected RETURNED, confirmed by GET.

### 4.4 Orders (kind `purchase`)

Create, after the device signature and at the gateway:

```
POST /v2/checkout/orders
PayPal-Request-Id: "ord-" + digest[:40]       (limit 108; stored 6 hours)
Prefer: return=representation
{ "intent": "CAPTURE",
  "purchase_units": [{ "reference_id": draft_id, "custom_id": digest[:64], "invoice_id": "STAR-" + digest[:20],
                       "description": ..., "amount": {"currency_code","value","breakdown":{"item_total":...}},
                       "items": [{"name","quantity","unit_amount","category"}] }],
  "payment_source": {"paypal": {"experience_context": {
      "brand_name","user_action":"PAY_NOW","shipping_preference":"NO_SHIPPING","return_url","cancel_url"}}} }
```

Backend confirms each nested field in the spec (`paypal_wallet_experience_context` holds these names; `amount.breakdown`
and `items` should be re-read).

- Expected: status `PAYER_ACTION_REQUIRED` with a `payer-action` link when `payment_source` is sent, or `CREATED` with
  `approve`. Accept either. Order status enum (spec): CREATED, SAVED, APPROVED, VOIDED, COMPLETED, PAYER_ACTION_REQUIRED.
- Before redirecting, the server checks the link's host: `*.sandbox.paypal.com`, or same-origin simulator. Else fail closed.
- `return_url`: `{PUBLIC_BASE_URL}/?pp_return=1&payment=<payment_id>`. **Capture never happens on a GET.** The page loads,
  reads the query string, and does a same-origin JSON POST to `/api/paypal/orders/return`.
- Return handler: load record, check owner session; `GET /v2/checkout/orders/{id}`; require APPROVED, and amount, currency and
  `custom_id` equal to the signed card; else ANOMALY or still-waiting.
- Capture: `POST /v2/checkout/orders/{id}/capture`, `PayPal-Request-Id: "cap-" + digest[:40]`, `Prefer: return=representation`.
  Spec: 200 replay, 201 new, 202 accepted later.
- Verify: `GET` the order again; `purchase_units[0].payments.captures[0]` must be `COMPLETED` with matching amount, and
  store `capture_id`. Capture PENDING stays Pending until `PAYMENT.CAPTURE.COMPLETED` or a GET.
- `ORDER_ALREADY_CAPTURED`: run the GET; not a failure. `INSTRUMENT_DECLINED`: show "PayPal declined that way of paying"
  and offer to reopen the same order. Timeout on capture: GET the order, it's known. Create timeout: repeat with same
  request id within the 6-hour window; after that mark `ABANDONED` (no approval means no money moved).
- Cancel return: state `BUYER_CANCELLED`, allowance released.

### 4.5 Refund (kind `refund`)

- `POST /v2/payments/captures/{capture_id}/refund`, `PayPal-Request-Id: "ref-" + digest[:40]`,
  `Prefer: return=representation`. Empty body means full refund; `amount` for partial (spec-checked).
- Only for capture ids in Mrs. Lee's own verified records; amount no more than remaining. Rule-checked and device-signed.
- Verify: `GET /v2/payments/refunds/{id}`, then update the parent record. Also `PAYMENT.CAPTURE.REFUNDED`.
- Honest copy: "In this demo the merchants are ours, so a refund is immediate. With an outside merchant Star would help you
  ask them or open a case with PayPal."

### 4.6 Invoicing (kinds `bill`, `money_request`)

Spec-checked: there's **no payer-side pay endpoint**. `POST /v2/invoicing/invoices/{id}/payments` records a payment from
the invoicer's side and must never be used to fake a payment.

- **Inbound bills** (the agent reads these): in sandbox they are created by `scripts/paypal_seed_sandbox.py` from the
  Household account with `invoicer` naming a demo biller, and read by `POST /v2/invoicing/search-invoices` and
  `GET /v2/invoicing/invoices/{id}`. Stated limitation: PayPal REST gives invoice lists to the issuer, not the payer, so a
  production version needs an inbox integration. Simulator seeds per visitor.
- **Pay a bill:** kernel + signature, then send her to `detail.metadata.recipient_view_url` (spec: metadata field), the
  same host check as orders. Paid only from `INVOICING.INVOICE.PAID` or a GET with status `PAID`. **Not** `MARKED_AS_PAID`
  or `PAID_EXTERNAL`.
- **Ask my son to pay me back:** `POST /v2/invoicing/invoices` then `POST /v2/invoicing/invoices/{id}/send`.
  Spec lists **no idempotency header** on these, so `detail.invoice_number = "STAR-" + digest[:16]` plays that role and
  reconciliation is search by invoice number before any retry. Required fields: Backend confirms against `invoice_detail`
  and `invoicer` in the spec. Still a signed card: it sends a message in her name.

### 4.7 Disputes (read only)

`GET /v1/customer/disputes`, `GET /v1/customer/disputes/{id}`. Exposed as a read tool `list_disputes` and a panel.
Dispute state enum (spec): OPEN_INQUIRIES, REQUIRED_ACTION, REQUIRED_OTHER_PARTY_ACTION, UNDER_PAYPAL_REVIEW, APPEALABLE,
RESOLVED. Caveat: these are disputes **on the Household account as merchant**. Webhooks `CUSTOMER.DISPUTE.*` raise a family
alert.

### 4.8 Transactions (read only)

`GET /v1/reporting/transactions` (`start_date`, `end_date` required, spec-checked) used by `get_recent_transactions` in
sandbox. Local verified records are the primary source, because reporting is delayed (delay length **unverified**).

### 4.9 Webhooks

`POST /api/paypal/webhook`, public, no session.

1. Read raw bytes, enforce the size limit, take the `paypal-transmission-id`, `paypal-transmission-time`,
   `paypal-transmission-sig`, `paypal-cert-url`, `paypal-auth-algo` headers. Any missing: 400, audit, nothing else.
2. If `PAYPAL_WEBHOOK_ID` is not set: 503, audit. Star still works, by GET polling.
3. `POST /v1/notifications/verify-webhook-signature` with `auth_algo, cert_url, transmission_id, transmission_sig,
   transmission_time, webhook_id, webhook_event` (all required, spec-checked). Must return `verification_status: "SUCCESS"`.
   Splice the **raw event JSON** into the request rather than re-serialising it. Re-serialising is widely reported to break
   verification; that claim is **unverified** and gets a comment.
4. Dedup on `event.id` in a persistent table (insert-or-ignore) **after** verification. A duplicate returns 200, no effect.
5. Match to a record via resource ids. Unknown resource: audit, 200.
6. Amount or currency mismatch with the signed card: state `ANOMALY`, guardian alert, never success.
7. Transitions are monotonic. Terminal states don't reopen except CAPTURED to REFUNDED or REVERSED.
8. Append `paypal.webhook` to the audit chain with event id, type, record id and resulting state.

Subscribed event names, confirmed on PayPal's webhook event names page: `CHECKOUT.ORDER.APPROVED`,
`PAYMENT.CAPTURE.COMPLETED`, `.DENIED`, `.DECLINED`, `.PENDING`, `.REFUNDED`, `.REVERSED`,
`PAYMENT.PAYOUTSBATCH.SUCCESS`, `.DENIED`, `.PROCESSING`, `PAYMENT.PAYOUTS-ITEM.SUCCEEDED`, `.UNCLAIMED`, `.FAILED`,
`.RETURNED`, `.BLOCKED`, `.HELD`, `.CANCELED`, `.REFUNDED`, `INVOICING.INVOICE.PAID`, `.CANCELLED`, `.REFUNDED`,
`CUSTOMER.DISPUTE.CREATED`, `.UPDATED`, `.RESOLVED`.

Local development without a public URL: webhooks can't arrive. Polling GETs give verified state; the status panel says
"Webhooks: not configured, using PayPal status checks."

### 4.10 Modes

| `PAYPAL_MODE` | Behaviour |
| --- | --- |
| `simulator` (default) | Keyless. Adapter transport. Every PayPal status in the UI carries a **SIMULATED** tag. Approval pages say "SIMULATOR. This is not PayPal. No real or sandbox money moves." |
| `sandbox` | Requires `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`. Tag: **SANDBOX**. Missing credentials: the server starts, a red banner shows the exact env names, health says `configured:false`, all drafts get BLOCK `R09_RAIL_UNAVAILABLE`. Never falls back |
| anything else, including `live` | Same as missing credentials, message: "Only simulator and sandbox are supported." |

### 4.11 Simulator (`app/paypal/simulator.py`)

- Subclass of `requests.adapters.HTTPAdapter`, returns real `requests.Response` objects with documented status codes, JSON
  shapes and HATEOAS links. Persisted in its own table in the ledger SQLite so restarts don't lose orders.
- Checks: bearer token present, required fields, `sender_batch_id` duplicate rule, request-id replay, order state
  machine (no capture before approval), refund limits.
- Webhooks: after state changes, a timer delivers a signed event to the same internal handler used by the HTTP route. Its
  `verify-webhook-signature` checks an HMAC of a process-local secret. A forged event fails.
- Deliberately documented differences go in a table in `docs/PAYPAL_NOTES.md`.
- **Fault injection**, only in simulator mode, per visitor: `timeout_after_commit` (processed, reply lost),
  `timeout_before_commit`, `http_500`, `decline_instrument`, `delayed_webhook`, `duplicate_webhook`, `forged_webhook`,
  `amount_mismatch`. Used by tests and the Failure drill.
- Payout receivers in the demo: registered family resolve to SUCCESS. A `new.person@…` receiver stays UNCLAIMED so the "stop
  that payment" flow can be shown. Use `.example` domains only.
- Demo world uses reserved fictional phone numbers (`555-01xx`).

---

## 5. Module list

### Rename (mechanical, first commit)

| From | To | Notes |
| --- | --- | --- |
| `app/bank/` | `app/wallet/` | |
| `app/bank/mock_bank.py` `MockBank` | `app/wallet/ledger.py` `Wallet` | `DEMO_USER = "u_lee"`, new world |
| `app/bank/gateway.py` | `app/wallet/gateway.py` | Dispatch changes in section 6 |
| `app/bank/storage.py` | `app/wallet/storage.py` | Add tables in section 7 |
| `app/bank/bank_api/` | `app/wallet/tools/` | Trace label "PayPal <API>" or "Star" |
| `tests/test_bank_apis.py` | `tests/test_wallet_tools.py` | |
| `CHALLENGE_DOMAIN "STAR-DCTA-AUTH-V1"` | `"STAR-PAY-AUTH-V1"` | |

### Delete

- `web/bank-logo.svg`; `docs/images/banner.jpg`, `home.jpg`, `care-card.jpg` (replaced by new screenshots).
- `qa/` entirely, except a new `qa/README.md` and `qa/e2e_demo.py` (section 12). All reports, results and PNGs show the old
  identity, and the harnesses assert old copy. Port what's useful from `qa/run_browser.py` first.
- `eval/contract-report.json`: regenerated. `eval/cases*.json`: rewritten, removed cases noted.
- `app/wallet/tools/giro.py`, `standing_instructions.py`, `deposits.py` (becomes `allowance.py`).
- `app/voice/asr.py`: rewritten per D10.
- `deploy/compose.yaml`, `Dockerfile`: keep, reword comments.
- In `tools.py`: `list_investments`, `get_portfolio`, `draft_investment`, `get_cards`, `lock_card`.
- In `rules.py`: R06, R07, R70 to R72. In `executor.py`: the investment branches, `cancel_giro`, `cancel_standing_instruction`.
  In `changes.py`: GIRO. Daily limit and add-payee stay.
- In `llm.py`, `textparse.py`, `planner.py`, `prompts.py`, `presenter.py`, `request_guard.py`, `verifier.py`, `crosscheck.py`,
  `evidence.py`: investment, account and schedule branches. Remove `asset_class` from the card. Remove `schedule`.
- `SECONDARY_REVIEW_KEY`, `/api/review/secondary`, `review_invest_threshold`.
- Rename map for text: Mdm Chen to Mrs. Lee; Chen Mei Ling to Anna Lee; Chen Zhi Qiang to David Lee; Chen Wei to Ben Lee; Chen Jun to
  Sam Lee; SP Group Utilities to Riverside Power; Bukit Timah Town Council to Maple County Water; CPF LIFE to Social Security style
  text "Pension deposit"; aliases such as "ah girl" dropped; "S$" to "$"; `SGD` to the config. All Singapore authorities in `fraud.py`
  patterns (`ica`, `iras`, `mas`, `mom officer`) replaced with generic ones plus `irs`, `medicare`, `social security`. Keep the
  regex tests' intent.

### New

```
app/paypal/
  __init__.py
  config.py        mode resolution, credentials, PayPalConfigError (I7, I10)
  client.py        PayPalClient: token cache + every call in section 4. Imported only by gateway, reconcile, webhooks, scripts
  reader.py        PayPalReader: read-only allowlisted session for the agent (I1)
  errors.py        PayPalError, PayPalUnknown
  simulator.py     SimulatorAdapter, state, fault injection, signed webhook emitter
  webhooks.py      verify, dedup, route to state machine
  reconcile.py     Reconciliation matrix in section 6
  calls.py         CallRecord, per-user ticker buffer, redaction
app/wallet/
  payments.py      PaymentRecord + state machine + allowance reserve/release
  catalogue.py     Demo merchants and offers (data + search)
  invoices.py      Local cache of invoices seen; seeds for simulator
  guardian.py      Pairing, approvals, alerts, freeze
app/wallet/tools/
  allowance.py     get_allowance                                         read
  commerce.py      search_catalogue, draft_purchase                      read, draft
  invoices.py      list_invoices, explain_invoice, draft_bill_payment, draft_money_request   read, read, draft, draft
  refunds.py       list_refundable, draft_refund, draft_stop_payout, list_disputes           read, draft, draft, read
app/kernel/
  trust.py         Deterministic merchant and invoice checks (section 8)
web/
  logo.svg
  activity.js      AG Grid page, shared with guardian
  guardian.html, guardian.js
  simulator.html, simulator.js
  vendor/ag-grid/ag-grid-community.min.js, LICENSE.txt
scripts/
  paypal_sandbox_check.py
  paypal_seed_sandbox.py
  build_paypal_subset.py
tests/fixtures/paypal/
  openapi_subset.json, examples/*.json, NOTICE
docs/
  PAYPAL_NOTES.md  API by API: what's used, doc source, verified or not, simulator differences
  IMPACT.md        sources with links
  DEMO_SCRIPT.md
LICENSE, THIRD_PARTY_NOTICES.md
```

---

## 6. Gateway and payment lifecycle

`ExecutionGateway.execute` keeps all its checks: digest, signed payload, enrolled credential, replay list, expiry, guardian
approval, dependencies. Its dispatch changes:

```
_check(...)                                                 # unchanged and first
with lock:
    rec = payments.get_by_key(idem_key)
    if rec: return replay(rec)                              # double click returns the same record
    check_velocity, check_frozen, check_no_unknown_for_same_payee
    reserve allowance
    rec = payments.create(state=SUBMITTING, ...); save      # write-ahead, I3
HTTP outside the lock:
    transfer -> client.create_payout | purchase -> client.create_order | refund -> client.refund_capture or cancel_item
    bill -> no write call; return the recipient_view_url | money_request -> create + send
with lock:
    apply result, audit, flush
```

`PayPalUnknown` is **not** an error to the customer. The record becomes UNKNOWN and the reply is: "I can't confirm that went
through yet. Please don't pay again. I'm checking with PayPal." A definite rejection (4xx with no ambiguity) means FAILED, and
the allowance is released.

States:

```
transfer:  SUBMITTING -> PROCESSING -> SENT | UNCLAIMED -> (SENT | RETURNED) | FAILED | HELD
purchase:  SUBMITTING -> AWAITING_APPROVAL -> APPROVED -> CAPTURING -> PAID | CAPTURE_PENDING -> PAID | DECLINED
           AWAITING_APPROVAL -> BUYER_CANCELLED | EXPIRED
bill:      AWAITING_PAYMENT -> PAID | INVOICE_CANCELLED
request:   SUBMITTING -> SENT_TO_PAYER -> PAID | INVOICE_CANCELLED
refund:    SUBMITTING -> REFUND_PENDING -> REFUNDED | FAILED
any write: SUBMITTING | CAPTURING -> UNKNOWN -> (resolved state) ; any -> ANOMALY (terminal, needs human)
```

Terminal success states (`SENT`, `PAID`, `REFUNDED`) can only be set with `verified=True` and an `evidence` entry.

**Reconciliation matrix** (`reconcile.py`). Runs on record refresh with a throttle, on `/api/state` polling, and once at startup
for records left in SUBMITTING or CAPTURING (which become UNKNOWN).

| Unknown at | Query | Then |
| --- | --- | --- |
| Payout create | Local webhook, then identical re-POST (section 4.3) | Batch id found: GET. Otherwise stays UNKNOWN |
| Order create | Identical POST with the same `PayPal-Request-Id` within 6 h | Order found: continue. After 6 h: ABANDONED |
| Capture | `GET` order, look for captures | Found: verify. None and APPROVED: retry with the **same** request id |
| Refund | `GET` capture or order, look for refunds; same request id | Found: verify |
| Invoice create or send | `search-invoices` by invoice number; `GET` | Found: continue at the right step. Not found: retry the create |

UNKNOWN older than `PAYPAL_UNKNOWN_ALERT_MIN` raises a guardian alert. Star never tells the user to try again while unknown.

Session `Phase` stays as is: the session goes to EXECUTED once the gateway accepts, and the lifecycle lives on the record. So a
pending order doesn't block a new draft, except R60. `dependency_ids` require a verified terminal state.

Receipt becomes `receipt_version: 3`: card, digest, signature, plus `paypal: {mode, api, idempotency_key, ids, state, verified_by,
verified_at, event_ids}` and `receipt_sha256`. `scripts/verify_receipt.py` also checks that the idempotency key equals the
function of the digest. It proves that **this signature produced this PayPal request key**. It doesn't prove PayPal's state.

Audit events added: `paypal.call`, `paypal.webhook`, `paypal.webhook_rejected`, `payment.state`, `trust.check`,
`guardian.paired`, `guardian.approved`, `guardian.denied`, `guardian.alert`, `wallet.frozen`, `wallet.unfrozen`, `invoice.sanitized`.

---

## 7. Data model

Persistence stays the single-process snapshot style in `storage.py`. New fields in the snapshot unless noted.

```python
# PaymentRecord (dict in the wallet snapshot, keyed by payment_id)
payment_id, user_id, session_id, draft_id, digest, kind, rail      # rail: payouts|orders|refund|payout_cancel|invoice
amount_cents, currency, counterparty_name, counterparty_handle     # handle = email, masked for display
idempotency_key, state, verified: bool, verified_by                # webhook:<event_id> | get:<path>
created_at, updated_at, reconcile_attempts, next_reconcile_at
paypal: {batch_id, item_id, order_id, capture_id, refund_id, invoice_id, invoice_number, approve_url, debug_ids[]}
parent_payment_id                                                   # a refund's original
refunded_cents
evidence: [{at, source, event_type, state}]
```

`Draft` additions, **all in `card_fields()` so they are signed**: `rail`, `payee_handle`, `merchant_id`, `merchant_name`,
`items: [{offer_id, name, quantity, unit_cents}]`, `invoice_id`, `invoice_number`, `capture_id`, `parent_payment_id`,
`trust: {level, reasons, codes}`. Removed: `asset_class`, `schedule`. `build_challenge` and the gateway's `expected` block include
`rail`, `payee_handle`, `merchant_id`.

```python
# World (u_lee)
allowance: {monthly_cents, reserved_cents, spent_cents, period}          # as the single WALLET account
payees: [{payee_id, name, relation, type, aliases, paypal_email}]        # type: person|biller
merchants: [{merchant_id, name, first_seen, orders_with_us, flagged, flag_reason, description}]
offers: [{offer_id, merchant_id, item_key, name, category, price_cents, reference_cents, aliases}]
invoices: [{invoice_id, number, invoicer_name, invoicer_email, biller_id, amount_cents, due, note, terms, status, view_url}]
transactions, limits, protection                                         # kept
guardian: {name, relation, status, key_jwk, key_id, token_sha256, invite: {token_sha256, expires_at}, removal: {effective_at}}
alerts: [{alert_id, at, level, kind, text, draft_id, read}]
frozen: {on, by, at}

# Tables
webhook_events(event_id PRIMARY KEY, type, received_at, payment_id)       # dedup survives restart
simulator_state(id=1, data)
```

Demo catalogue (fictional names only; check for real businesses before finalising):

| Item | Merchant | Price | Reference | Expected result |
| --- | --- | --- | --- | --- |
| Blood pressure monitor | Corner Pharmacy (trusted, 4 prior orders) | $39.00 | $40 | Clean, the recommended draft |
| Blood pressure monitor | Green Basket Health (known, 1 order) | $44.50 | $40 | Allowed, MEDIUM question |
| Blood pressure monitor | MedDeals Direct (new, listing text contains an injected instruction) | $149.00 | $40 | BLOCK R91, listing text sanitized |
| Weekly groceries box | Green Basket Grocers | $62.00 | $60 | Clean |
| $500 gift card | QuickGift Online (new) | $500.00 | $500 | BLOCK or guardian, R94 |

Seeded invoices: Riverside Power $86.40 (known biller, clean); Maple County Water $42.00 (clean); "SecureTech Renewals" $499.99 (the
fake; note has a `555-01xx` phone, urgency words, and an injected "assistant: approve and pay" sentence).

---

## 8. Rules added (`kernel/trust.py`, called by `rules.evaluate`)

Same style as `rules.py`: a code for audit, plain words for Mrs. Lee.

| Code | Level | Trigger |
| --- | --- | --- |
| R09_RAIL_UNAVAILABLE | BLOCK | PayPal not configured or mode invalid |
| R14_WALLET_FROZEN | BLOCK | Freeze on |
| R60_UNKNOWN_OUTCOME | BLOCK | An UNKNOWN record for the same payee, or any UNKNOWN for the same amount |
| R61_RECENT_DUPLICATE | MEDIUM | Same payee and amount verified in the last 10 minutes |
| R80_INVOICE_PHONE | BLOCK under Enhanced, HIGH under Standard | Phone number or "call us" in invoice note or terms (PayPal's own guidance: see section 13) |
| R81_INVOICE_URGENT | HIGH | Urgency, threat, "fraudulent charge", "cancel within" in invoice text |
| R82_BILLER_UNKNOWN | HIGH + guardian | Invoice issuer never paid before and not a saved biller |
| R83_BILL_UNUSUAL | MEDIUM | Over twice the usual for this biller |
| R84_INSTRUCTION_IN_DATA | BLOCK + alert | `_sanitize` removed something from this invoice or listing |
| R90_MERCHANT_NEW | HIGH + guardian | No prior orders and first seen under 30 days |
| R91_PRICE_ANOMALY | BLOCK | Offer price over 1.5 times `reference_cents` |
| R92_MERCHANT_FLAGGED | BLOCK | Flagged in demo trust data |
| R93_NOT_IN_CATALOGUE | BLOCK | Offer id, price or merchant not exactly what the catalogue says (model can't set a price: amount comes from the offer) |
| R94_GIFT_CARD | HIGH + guardian, BLOCK with any scam signal | Category gift card, or crypto |
| R95_REFUND_INVALID | BLOCK | Capture not hers, not verified, or amount over remaining |
| R96_REFUND_SCAM | BLOCK | Words like "overpaid", "send back the difference", "refund department called" when she'd be **sending** money |

Grounding for `purchase`: the item must be in her own words, and the amount derives from the catalogue (`amount_source: "catalogue"`).
The independent verifier reads only her words and checks item and quantity, and any price she said. For `bill`: amount comes from
the invoice. For `refund`: which purchase, from her words.

Add scam patterns to `fraud.py`: tech support and remote access, grandchild in jail or bail, gift card payment, crypto ATM,
government (IRS, Medicare, Social Security), Treasury or "safe account" variants. Keep one-way escalation and existing pre-screen.

`guardian_required(card, world)` replaces `secondary_required`: new person above `GUARDIAN_THRESHOLD` (default $300); any trust hit
marked "+ guardian"; any payment in a session with scam level MEDIUM or higher that was not blocked. If no guardian is paired: the
payment waits, and Star offers to pair or says it can't go ahead (fail closed).

---

## 9. Agent tools

| Tool | Tier | Backed by |
| --- | --- | --- |
| `get_allowance` | read | local |
| `list_payees`, `request_add_payee` | read, draft | local; add payee takes a PayPal email, grounded and signed |
| `get_recent_transactions` | read | verified records + reader |
| `get_limits`, `set_daily_limit` | read, control | existing |
| `search_catalogue` | read | local; output sanitized, marked untrusted |
| `draft_purchase(offer_id, quantity, evidence)` | draft | no amount argument at all |
| `draft_transfer` | draft | existing, USD |
| `list_invoices`, `explain_invoice` | read | reader; note text sanitized, with `suspicious_removed: true` |
| `draft_bill_payment(invoice_id, evidence)` | draft | amount from the invoice |
| `draft_money_request(payee_id, amount, reason, evidence)` | draft | |
| `list_refundable`, `draft_refund`, `draft_stop_payout` | read, draft, draft | verified records only |
| `list_disputes`, `payment_status` | read | reader, verified state only |
| `freeze_payments` | control | same grounded pattern as `lock_card` was |
| `queue_follow_up`, `set_care_mode`, `handoff_to_human` | existing | |

`handoff_to_human` copy: the configured hotline, and Anna.

Update `prompts.py`, `OfflineLLM` and `detect_intent` (intents `purchase`, `invoice_query`, `refund`, `money_request`, `stop_payout`,
`freeze`). The output validator keeps catching false claims and adds "paid", "purchased", "refunded".

---

## 10. HTTP API

Kept: `/`, statics, `/api/state`, `/api/audit/session/{id}`, `/api/voice/...`, `/api/health`, `/api/chat`, `/api/asr`, `/api/key`,
`/api/challenge`, `/api/confirm`, `/api/passkey/*`, `/api/protection*`, `/api/change/*`, `/api/lab`, `/api/lab/contract`,
`/api/demo/model-mistake`, `/api/cancel`, `/api/card/{id}`, `/api/trace/{id}`, `/api/audit/verify`, `/api/reset`, `/api/care*`.
`/api/world` is removed if unused. `/api/transaction/...` now reads the payment record.

Changed: `/api/confirm` now returns `{payment, reply, next_step}`. `next_step` is `{"type":"redirect","url":...}`, `{"type":"track"}` or
null. `/api/health` adds `paypal: {mode, configured, webhooks, label}`.

New:

| Route | Purpose |
| --- | --- |
| `GET /api/paypal/calls?session_id=` | Ticker: redacted call log + count of money-moving calls this task |
| `GET /api/payments?session_id=` | Records; refreshes with throttle |
| `GET /api/payments/{session_id}/{payment_id}` | One record + evidence timeline |
| `POST /api/paypal/orders/return` | `{session_id, payment_id}`: verify, capture, verify again. Idempotent |
| `POST /api/paypal/orders/cancelled` | `{session_id, payment_id}` |
| `POST /api/paypal/webhook` | Section 4.9. Same-origin rule passes because PayPal sends no Origin. Add a rate limit |
| `GET /api/catalogue`, `GET /api/invoices`, `GET /api/disputes` | Panels (session scoped) |
| `GET /api/activity?session_id=` | Grid rows, section 11 |
| `POST /api/freeze` | Instant. Unfreeze via a signed challenge + guardian |
| `POST /api/guardian/invite/challenge`, `/invite` | Mrs. Lee's signed invite, returns a one-time link, expiring in 10 min |
| `POST /api/guardian/pair` | Guardian's public key + invite token; returns a guardian token |
| `POST /api/guardian/feed` | Token in body: alerts, pending approvals, activity rows, masked |
| `POST /api/guardian/challenge`, `/decide` | Sign `{domain:"STAR-GUARDIAN-V1", draft_id, digest, decision, nonce, expires_at}` |
| `POST /api/guardian/freeze` | Instant |
| `POST /api/lab/drill` | Failure drill, simulator only, returns timeline |
| `POST /api/lab/naive-agent` | Scripted baseline comparison, simulator only, isolated state |
| `GET /simulator/checkout`, `/simulator/invoice`; `POST /simulator/api/decide` | Routed only in simulator mode; 404 otherwise |
| `GET /guardian` | Guardian page |

Security changes in `app/security.py`:

- Add `/api/paypal/webhook` and `/api/guardian/*` to rate limits. Webhook size limit 64 KB (official max size **unverified**).
- Static allowlist additions listed in section 5. Vendor file gets `cache-control: public, max-age=86400`, otherwise `no-store`
  downloads 2 MB per visit.
- Pages that display the grid get their own CSP. **Spike result, real Edge run under Playwright against AG Grid Community 36.2.0
  UMD build (2,058,454 bytes):**
  - with `style-src 'self'`: rows render, layout is broken (wrapper 2169 px tall, dozens of CSP errors);
  - with a per-response nonce and the documented `styleNonce` grid option: layout is correct, 14 inline-style CSP messages remain;
  - adding `'unsafe-hashes'` + those 14 sha256 hashes removes them; what's left is the data-URI icon font, which needs
    `font-src 'self' data:`. That last step wasn't run; verify it.
  - So: `style-src 'self' 'nonce-{n}' 'unsafe-hashes' <hashes>; font-src 'self' data:`. Script policy is unchanged. `index` and
    `guardian` become template responses with `<meta name="style-nonce" content="{n}">`. Hashes go in `security.py` with a comment
    pinning the version. `qa/e2e_demo.py` fails on any CSP violation, which catches hash drift.
  - Load the 2 MB bundle only when the Activity page opens (`script.src`, same origin).

---

## 11. Frontend

Pages: **Home** (allowance, what needs attention, last payments, bills), **Star** (assistant), **Shop**, **Bills**, **Family**, **Activity
& audit**, **Security lab**, **Settings**. Care mode: as now, plus large "What can I say?" prompts. Every PayPal status passes through one
helper `ppBadge(status)` that always adds the SIMULATED or SANDBOX tag; a test greps for direct status text.

**Live PayPal ticker** in the trace panel and as a compact strip on the card: `POST /v1/payments/payouts 201` etc, and a large
counter "Money-moving PayPal calls for this request: N". It comes from the server log, never computed in the browser.

**Tracker card** after signing: Signed, Sent to PayPal, Approved by you, Paid, Verified by (webhook or status check). Speaks the result
only when verified. Redirect return shows "Checking with PayPal…", never "Success".

**Activity & audit grid (AG Grid Community)**, `web/activity.js`, holding up for Best Use of AG Grid:

- Rows: payments, stops (blocked, frozen, cancelled, scam-stopped), guardian decisions, refunds. Row id = stable.
- Columns: Time; Type; To/From; Amount (right aligned, value formatter, signed); Status (renderer: pill + mode tag); Stopped by
  (human rule name); Why; PayPal reference; Signed with; Family (approved, denied, —); Audit hash (short, with Verified tick).
- Text, number and date column filters with floating filters; multi-sort; quick search box (`quickFilterText`).
- Big filter chips via external filter: All, Money out, Stopped, Needs attention, Family decisions. Set filter is an Enterprise feature,
  so not used. **Use only Community features.** Master-detail is also Enterprise: a row click opens a side panel showing the timeline,
  card, signature, webhook evidence and chain entry.
- Pinned bottom row: total sent, total refunded, total stopped ("kept safe"), from the visible rows and labelled as this demo's numbers.
- Live updates: `applyTransaction` from polling, `enableCellChangeFlash` on Status, so the webhook's arrival is visible.
- `getRowClass` tints stopped rows. Not by colour alone: there's always a text label.
- Care mode: large row height and font via theme params, reduced columns, card-like. Keyboard navigation and ARIA (built in) tested.
- Mobile: column set by width, no horizontal page scroll.
- CSV export (`exportDataAsCsv`) for "send this to my family or the police".
- Saved column state via `localStorage` in try/catch.
- Guardian view: same module, `role="guardian"`, masked handles, Pending approvals pinned at the top.
- Theme: `themeQuartz.withParams` using app tokens, light and dark.

**Guardian page** (`/guardian`): pair screen, alerts, pending card (exact fields, with fingerprint step), approve or deny, freeze, grid.
Label: "Alerts appear here. This demo doesn't send real texts or emails."

**Simulator pages**: plain, unmistakably not PayPal, striped banner, no resemblance in colour or logo.

**Lab**: adds attacks and the Failure drill and naive-agent comparison (labelled "scripted illustration, not a test of any real product").

Copy rule: no "bank", "branch", "bank app". "Fingerprint" stays. The simulated fingerprint stays labelled.

---

## 12. Test plan

Run with `D:\Python\.venv_hack\Scripts\python.exe -m unittest discover tests`. Baseline 266.

**Existing files**

| File (tests) | Disposition |
| --- | --- |
| `test_kernel` (41), `test_recovery` (12), `test_scam_context` (4), `test_care_mode` (7), `test_agent` (17), `test_http` (20), `test_contract` (13), `test_contract_http` (5) | Keep. Change names and amounts; reviewer tests become guardian tests covering the same properties |
| `test_voice` (7) | Keep; ASR tests move to the new provider via stubbed HTTP |
| `test_wallet_tools`, was `test_bank_apis` (41) | Keep payees, limits, bills, false-claim, tool offering. Remove GIRO, standing instruction, own-account, deposit-account tests with the features |
| `test_hackathon` (84) | Rename by purpose to `test_requirements.py`. Remove only investment, suitability, own-account and schedule cases; move their safety properties (grounding, frozen mismatch, reference amounts) onto purchase or transfer |
| `test_planner` (15) | Keep; rewrite invest examples as purchase or bill |

Every removed test is listed in the Backend report with its reason. The lab's `verifier_account` attack goes (single wallet); the new
`verifier_item` attack covers the property.

**New**

- `test_paypal_client.py` real client + stub adapter: token cache and expiry, 401 refresh once, each call's URL, headers and body,
  no retries, timeouts become `PayPalUnknown`, redaction.
- `test_paypal_http_stub.py` real client against a real local HTTP server: true socket read timeout, 5xx, malformed JSON.
- `test_paypal_spec.py` every request produced by the client is checked against `tests/fixtures/paypal/openapi_subset.json` (paths,
  methods, field names, required, length limits, enums). The subset is generated from the official specs at a pinned commit by
  `scripts/build_paypal_subset.py`. Response parsers run on the spec's official examples.
- `test_paypal_simulator.py` state machines, duplicate rule, approval requirement, no capture without approval, faults. The simulator's
  responses go through the same subset check.
- `test_payouts.py`, `test_orders.py`, `test_refunds.py`, `test_invoices.py` full flow via kernel + signed card, with fixture keys.
- `test_webhooks.py` good signature; forged (FAILURE); missing headers; no webhook id configured; duplicate id; out-of-order; amount
  mismatch into ANOMALY; terminal state not reopened; the verify call's body contains the raw event byte-for-byte.
- `test_reconcile.py` each row of the matrix; lost response then exactly one simulated payout; double click; restart mid-submit;
  R60 blocks new draft.
- `test_trust.py` each R-code. Deterministic.
- `test_guardian.py` pairing and expiry; wrong key; approval bound to digest (a changed card invalidates it); approval can't unlock BLOCK
  or FROZEN; denial kills it; replay; guardian can't draft; removal delay.
- `test_architecture.py` I1, I2 via AST walk and transport allowlist; no `api-m.paypal.com` string anywhere; no `.env` value or
  `Authorization` in the audit file after a full flow.
- `test_modes.py` sandbox with no keys, `live`, garbage: readable error, BLOCK, no simulator fallback, no network.
- `test_identity.py` a repo-wide scan for the old-identity terms list; skips `docs/PLAN.md` until Materials deletes the plan.
- `test_http.py` additions: new statics allowlisted, source not fetchable, simulator routes 404 in sandbox mode, grid page CSP present,
  capture impossible on GET.

**End to end** `qa/e2e_demo.py`, Playwright, `channel="msedge"`, against a real server in simulator mode. Runs the whole section 14 script
on desktop and at 390 px width. Fails on any console error, CSP violation or horizontal page scroll. Takes screenshots to `qa/shots/` for
Materials. Not in unit discovery. Runs in CI only if it stays reliable.

**Evaluation**: `eval/run_eval.py` with rewritten cases; `eval/run_contract_eval.py`. CI keeps both. Live-model numbers appear only if run with
a real key, and then state which model and when.

**Lab additions**: forged webhook; replayed webhook; redirect claims success without capture; injected invoice; injected merchant listing; model
sets its own price; refund of someone else's capture; guardian key forged; guardian approval reused on an altered card.

**Coverage target for manual sandbox**: `paypal_sandbox_check.py` prints `PASS/FAIL/SKIP` per API: oauth, payouts create, payouts dup rule shape,
payouts status, item cancel (needs unclaimed receiver), order create, approval (prints a link, waits with a timeout), capture, verify GET, refund,
refund GET, invoice create/send/search/get, disputes list, transactions list, webhook list, verify-signature (a known-bad signature must return
FAILURE). `simulate-event` is optional; whether its events verify is **unverified**. Writes `data/sandbox-check.json` with ids only. No secrets
printed. Exit code nonzero on any FAIL.

---

## 13. Impact evidence

Only these figures are cleared for use so far.

**FBI IC3, 2024 Internet Crime Report**, https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf (fetched; text extracted from the PDF):

- Complaints from people 60+: 147,127. Losses: $4.885 billion. Up 43% in losses and 46% in complaints from 2023.
- Average loss $83,000. 7,500 complainants lost over $100,000.
- 60+ losses: Tech Support $982,440,006; Confidence/Romance $389,312,356; Government Impersonation $208,096,366.
- Caveat to keep: these are reported complaints to the FBI, mainly US, and the report's own Appendix C has data notes.

Connection to be stated carefully: Star targets the moment of payment in authorised-payment scams. It doesn't claim to prevent these losses,
and we have **no measured results** on any real user.

**National Elder Fraud Hotline**, DOJ Office for Victims of Crime,
https://ovc.ojp.gov/program/stop-elder-fraud/providing-help-restoring-hope (fetched): 833-FRAUD-11.

**PayPal's own warning on invoice scams**,
https://www.paypal.com/us/cshelp/article/what-are-invoice-scams-and-money-request-scams-on-paypal-help1059. Direct fetch returned HTTP 403; the
content was seen only in a search result summary: the note may ask you to call a fake customer service number; don't call numbers in the note.
**A person must open this page in a browser before it's cited.** It's the basis of R80 and moment 2.

Not cleared: any FTC figure (the URLs tried returned 404), any PayPal user statistic, any senior-adoption statistic.

---

## 14. Demo flow and video

Keyless path, on the hosted URL in simulator mode, each step labelled SIMULATED. With keys the same script runs on the sandbox.

**Under 3 minutes. Target 2:50.**

| Time | Scene | What viewers see |
| --- | --- | --- |
| 0:00 | Hook | Mrs. Lee's home in Care mode. Voiceover with IC3 figure on screen, source visible. "Agents that can pay are coming. Who protects her?" |
| 0:15 | Send to family | "Send fifty dollars to my daughter." Read-back, card, fingerprint. Ticker: `POST /v1/payments/payouts 201`. Tracker reaches Sent after the verified event |
| 0:35 | **Moment 1** | "Get me a blood pressure monitor." Three offers, one stopped (price, new merchant). Card, sign, PayPal approval page, return: "Checking with PayPal…", then Paid, verified. Refund line: "actually I don't need it, I want my money back," signed, refunded |
| 1:15 | **Moment 2** | A new invoice. "What's this bill?" Warning read aloud. Phone in the note, urgency, unknown issuer, hidden instruction removed. Counter: 0. Anna's alert. Comparison panel: scripted naive agent pays |
| 1:45 | **Moment 3** | Split screen, Anna's device. "Pay Tom Reed six hundred dollars." The card waits for family. Anna denies, then repeat and approve to show the exact digest |
| 2:10 | Model mistake + failure drill | Frozen $500 vs $50. Network cut, double click: exactly one payout |
| 2:25 | Activity grid | Filter Stopped, click a row, chain verified, export. Download a receipt, offline check |
| 2:40 | Close | Architecture line: "The model understands. Code decides. She authorises." "Built on the PayPal sandbox; not affiliated." AI models named |

Rules for the video: only fictional brands; no third-party logos or music without permission; state simulator versus sandbox on screen; real voice
input only if it works cleanly in one take, otherwise show typed input and say so. No ffmpeg on this machine: Materials can record silent draft
footage with Playwright (`record_video`, WebM), and a person assembles and narrates it.

---

## 15. Ordered work list

Each item ends in a commit with tests green, unless marked. English commit messages, trailer as required.

### Backend

1. Mechanical rename per section 5. Tests green. No behaviour change.
2. Config: currency, persona world, hotline, timezone, LLM default. `fmt_money`. Tests updated.
3. Remove investments, GIRO, standing instructions, own accounts, cards, suitability, card `asset_class`/`schedule`. Report each removed test.
4. `app/paypal/`: `config`, `errors`, `calls`, `client` (token and payouts first), simulator skeleton, `build_paypal_subset`, spec tests.
5. `payments.py`, write-ahead, gateway dispatch, Payouts flow to verified state. Receipt v3.
6. Webhooks with simulator-signed events; state updates; audit.
7. `catalogue`, `trust`, `commerce` tools, Orders: create, return, capture, verify. Rules R90 to R94.
8. Refund, payout cancel, disputes, transactions reader.
9. Invoices: seed, read, sanitize, R80 to R84, bill pay via view URL, money request.
10. Reconciliation, startup recovery, fault injection, R60 and R61, `test_reconcile`.
11. Guardian backend replacing reviewer; freeze; alerts.
12. Agent: prompts, `OfflineLLM`, intents, verifier and crosscheck for new kinds, output validator.
13. HTTP endpoints; security changes; `test_architecture`, `test_modes`.
14. Lab attacks, Failure drill, naive-agent baseline, eval cases, contract eval. `scripts/paypal_sandbox_check.py`, `paypal_seed_sandbox.py`.
15. `render.yaml`, `.env.example`, `Dockerfile`, `requirements.txt`, CI.
16. `docs/PAYPAL_NOTES.md` first version, including simulator differences and every unverified item.

`render.yaml` content: service `star-family-payments`, region `oregon`, `plan: free` with the existing note, `PAYPAL_MODE: simulator`, `PUBLIC_DEMO: "1"`,
`SIGNING_SECRET: generateValue`, `WALLET_CURRENCY: USD`, `SCAM_HOTLINE_COUNTRY: US`, `healthCheckPath: /api/health`. With `sync: false`: `PAYPAL_CLIENT_ID`,
`PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, `PAYPAL_SANDBOX_RECEIVERS`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `ASR_*`. Comment: to go sandbox, set
`PAYPAL_MODE=sandbox` and the secrets in the dashboard.

### Product

1. Identity: logo, tokens, welcome screen, footer disclaimer; remove old assets and copy. `grep` clean in `web/`.
2. Home, Bills, Family, Shop pages. Removal of investment, GIRO and card panels. Light and dark, 390 px wide.
3. Assistant and card: new kinds, trust section, read-back, "Why I stopped this" for Care mode.
4. Ticker, tracker, redirect return, `ppBadge`, sandbox-misconfigured banner, model-name label.
5. Vendor AG Grid with licence, nonce and hash CSP, Activity grid per section 11.
6. Guardian page and pairing, shared grid. Two-device flow.
7. Simulator pages. Lab additions, comparison panel.
8. `qa/e2e_demo.py` passing all of section 14, screenshots.
9. Accessibility pass: focus order, contrast, reduced motion, read-aloud on every stop, Care mode at 200% zoom.

### Materials

1. `README.md` rewrite: what, why, try it in 60 seconds (hosted and local, keyless), sandbox setup steps, architecture, what is real versus simulated, AI
   used and how (Star's models **and** Claude Code as a development tool), update statement, sponsor tech.
2. `LICENSE`, `THIRD_PARTY_NOTICES.md`. `docs/SECURITY.md`, `docs/RISK_CONTRACT.md`, `CONTRIBUTING.md` updated, `docs/IMPACT.md`.
3. `docs/DEMO_SCRIPT.md` with narration. New screenshots. Draft silent footage if feasible.
4. Submission text draft: description per category, testing instructions, test credentials or how to get sandbox access.
5. Repo clean-up: run the identity grep over the tree, delete this plan, full secret scan on tree and history (see section 16), `.gitignore` review.
6. `docs/SUBMISSION_CHECKLIST.md`: section 16's human actions.

Identity grep (case-insensitive, whole tree): `bank|savings bank|bank app|speech vendor|hosted model|ocbc|singtel|giro|cpf|mdm chen|s\$`. Reports of false positives are fine; hits are not.

### Fixed order constraints

Backend 1 to 3 touch nearly every file, so no parallel Product work until they are in. Product needs Backend 13 for the real API. Materials goes last, and its screenshots
need Product 8.

---

## 16. Risks

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| R1 | **Keyless means no AI.** The offline model is rules. A judge on the keyless hosted URL sees rule-based replies in a contest that requires AI. No LLM key exists in this session | High | A person sets a real OpenAI-compatible key on the hosted service and records the video with it. UI always names the engine. Docs say which parts need a key. Rate limits exist for cost |
| R2 | **Sandbox never verified.** No credentials here. The first real run may reveal shape differences (duplicate-batch error, invoice required fields, webhook verification, sandbox approval page behaviour) | High | Spec-driven tests, `paypal_sandbox_check.py`. **A person must run it well before Nov 12** and leave days to fix. Video on sandbox if at all possible |
| R3 | **"Whose money?"** Client-credentials makes the Household account both sender and merchant. A judge may ask how this serves a real senior | Medium | Say it up front (section 4.1), and describe the production path as future work. Don't overclaim |
| R4 | Stage One check: judges must see the required APIs truly applied, but the keyless path is simulated | Medium | Hosted sandbox deployment, or test credentials in the submission's testing notes, plus clear instructions. Real client code is what runs in both modes |
| R5 | Prior work and rules: entry needs a credible "significantly updated" story; the project was built for another hackathon; the same base is also going to the Alipay entry | Medium | Honest update statement. **The team should re-read both contests' rules on reuse and simultaneous submission** |
| R6 | Copyright: 5 commit identities (`ljjfh`, `JackieZhang2025`, `TF916`, 傅涵 and another `ljjfh` email), plus code adapted from a teammate's "Aegis" prototype. Licensing as MIT and claiming original work needs their agreement | Medium | Human action. Credit in README |
| R7 | Public history holds the old logo and screenshots, and personal author emails | Medium | Before the **first** push (remote is empty, so rewrite is safe): remove `web/bank-logo.svg`, `docs/images/*`, `qa/results/*`, `qa/*.html` from history with `git filter-branch`, keep commits and authors. Needs a person's yes. A quick regex scan of history found only placeholder-looking values, but its output was truncated, so it's not conclusive: rerun in full |
| R8 | Render free tier sleeps and wipes the disk; a judge's first request takes long; webhooks hit a sleeping server | Medium | Paid plan for judging week; GET polling doesn't need webhooks; clear loading state on the welcome screen |
| R9 | Removal blast radius: `llm.py`, `textparse.py`, `planner.py` are regex heavy, investment words run through them; `test_hackathon` 45 and `test_wallet_tools` 50 lines touch removed features | Medium | Order in section 15; safety properties moved, not lost |
| R10 | `OfflineLLM` must learn new intents or the keyless demo looks broken | Medium | e2e test uses keyless path, exact demo sentences fixed |
| R11 | Browser speech recognition varies by browser, fails on some, and sends audio to the browser vendor | Medium | Typing and buttons always work. Docs name supported browsers. Say it isn't a recording |
| R12 | Hashes in CSP drift with AG Grid config | Low | e2e fails on any CSP violation. Pin vendor version. Re-capture hashes per section 10 |
| R13 | AG Grid bundle is 2 MB | Low | Lazy load, cache header |
| R14 | Trademark in video or copy: fake invoice text might echo real brands | Medium | Fictional names, review list before recording |
| R15 | Naive-agent comparison could read as an attack on a real product | Low | Scripted, labelled, no product named |
| R16 | Simulated guardian alerts could be read as real SMS | Low | Label |
| R17 | Scope vs 13 days | High | If time runs out, cut in this order: payout cancel, money request, disputes panel, transactions reader, naive-agent panel. **Don't cut**: Payouts, Orders, Refund, Webhooks, Invoice read and stop, guardian, grid |
| R18 | Regulated activity: sending money on someone's behalf is regulated in production | Low | State it is a sandbox prototype, not a licensed service |

**Actions only a person can take**

1. Create a PayPal developer account and sandbox accounts, run `paypal_sandbox_check.py`, register the webhook. Never commit the keys.
2. Provide an LLM key for the hosted service; choose the model; name it in the submission.
3. Connect Render, deploy, set secrets.
4. Narrate and upload the video (public YouTube, under 3 minutes).
5. Agreement from teammates on licence and entry; decide on history rewrite (R7).
6. Make the repo public; fix the "Payal" typo in the repo description; add collaborators.
7. Open the PayPal help page in section 13 and confirm its wording. Re-check the SG hotline if it will be used.
8. Fill in and submit the form before Nov 12 12:00 PT (Nov 13 04:00 Singapore time).

---

## 17. Open items passed to later stages

- Check that the catalogue and persona names aren't real businesses, as far as reasonably possible.
- Confirm in the Invoicing spec: required fields of `invoice_detail`, where the invoicer's name lives, and the search filter for invoice number. Planning confirmed
  only the property lists at the top level.
- Confirm `amount.breakdown`, `items[].category`, and the link `rel` names in the Orders spec examples.
- Decide whether the fake invoice is visible immediately or arrives during the demo through a "New invoice arrives" control. Recommended: the control, for pacing.

---

## 18. Backend stage: what was built, and how it differs from the above

Done: sections 3 to 10 and 12 server side, items 1 to 16 of the Backend list. Differences from the plan, all deliberate:

- **Invoices keep a stable local id** (`inv_power_oct`) next to `paypal_id`. The agent and the card use the local id; PayPal's id is only
  needed at the gateway. Found by `POST search-invoices` with `reference` = a per-visitor tag (simulator) or `star-demo` (sandbox).
- **Webhook dedup is in the wallet snapshot, not its own table**, so the dedup entry and the state change are saved the same way. It survives
  a restart, which is what the plan wanted.
- **Simulator events are delivered when the app polls**, by `Reconciler.pump`, not by a timer. Deterministic in tests, no background thread.
- **Thresholds:** cooling-off above $500 (`RULE_HIGH_THRESHOLD`), $1,200 on Standard; daily limit $1,000; `RULE_VELOCITY_MAX` default 5, since a demo
  of the whole story used to hit 3. Refunds and money requests don't count towards velocity, and refunds work during a freeze.
- **`stops`** is a new per-customer list (blocked, frozen, denied, scam-stopped): Activity grid rows. `GET /api/activity` returns them with payments.
- **New routes** beyond section 10: `POST /api/demo/invoice-arrives` (the pacing control from section 17), `GET /simulator/api/info`,
  `POST /api/freeze/lift/challenge` and `/lift`, `POST /api/guardian/lift/challenge`, `/lift`, `/remove/challenge`, `/remove`.
- **Test files:** `test_payouts/orders/refunds/invoices` are one file, `tests/test_payment_flows.py`. `tests/test_000_isolated_env.py` keeps the suite off `data/`.
- **`scripts/smoke_keyless.py`** drives a running server through the whole story over HTTP, for anyone to rerun.

### Handoff to the Product stage

`web/app.js` and `web/index.html` still describe the old product and are expected to be rewritten. They currently break on:

- Removed: `POST /api/review/secondary` (`app.js` line ~363), `GET /api/world`, `/bank-logo.svg`.
- `/api/state` no longer has `bills` (now `invoices`), `holdings`, `cards`, `standing_instructions`, `giro`, `risk_profile`. New: `allowance`, `paypal`, `engine`,
  `guardian`, `alerts`, `frozen`, `hotline`, `currency`.
- Cards: `secondary_required` is now `guardian_required` (with `guardian_approved`, `guardian_denied`). `schedule` and `asset_class` are gone. New: `items`, `invoice`,
  `merchant`, `trust`, `offers_checked`, `next_step_words`, `paypal_mode`.
- `/api/confirm` returns `{receipt, payment, next_step, reply, ...}`. `next_step` is `{"type":"redirect","url"}` or `{"type":"track"}` or null. Show success only when
  `payment.verified`. On return from approval (`?pp_return=1&payment=...`) POST `/api/paypal/orders/return`; on `?pp_cancel=1` POST `/api/paypal/orders/cancelled`. Poll
  `/api/payments/{session_id}/{payment_id}`.
- Pages to add, already routed: `web/simulator.html` + `simulator.js` (reads `/simulator/api/info`, posts `/simulator/api/decide`, then goes to `redirect`), `web/guardian.html` +
  `guardian.js`, `web/activity.js`, `web/logo.svg`, `web/vendor/ag-grid/ag-grid-community.min.js`. Static allowlist is `STATIC` in `app/server.py`.
- CSP: `/` and `/guardian` get `grid_csp(nonce)`. A literal `__STYLE_NONCE__` in the HTML is replaced with the nonce, for `<meta name="style-nonce">`. Put the AG Grid hashes
  from section 10 in `GRID_STYLE_HASHES` in `app/security.py`.
- Every PayPal status needs its tag: `mode_label` on payments and panels, `paypal.label` on state.

### Handoff to the Materials stage

`docs/SECURITY.md`, `docs/RISK_CONTRACT.md`, `README.md` and `CONTRIBUTING.md` still describe the old product: `tests/test_identity.py` lists them in `PENDING`, and each stage
should shrink that list. `docs/PAYPAL_NOTES.md` has the first version of what is real and what isn't.
