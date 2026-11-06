# Star — Devpost submission draft

Replace every `[TEAM: ...]` before submitting. Local simulator footage is labelled SIMULATED and uses offline rules. It does not establish real PayPal sandbox or online AI operation.

## Project name

Star — voice payments with a family safety check

## Tagline

Say what you need. Check the payment. Let family help when something looks wrong.

## Inspiration

An older adult should be able to ask for a payment in ordinary words without handing an AI unrestricted authority over money. A scammer can persuade a person, and can put instructions inside a merchant listing or invoice. We built Star around that problem: the model can be mistaken, so the payment path must check more than its answer.

## What it does

Mrs. Lee is a fictional customer. She can send money to family through Payouts, choose a product through Orders, check an invoice, request repayment and refund a purchase. Star builds a plain-language card from fixed data and asks her device to sign it. New recipients and other risky payments also need her daughter Anna's signature on the same card. A family guardian can refuse or freeze payments.

The demonstration has three main moments: a purchase chosen from checked offers, a fake support invoice stopped before a payment call, and a new-person payment waiting for Anna on another device. Activity & audit combines payment states and stops in an AG Grid table. It can filter, export, open a timeline and download a receipt whose digest and device signature can be verified offline.

## How we built it

Python and Starlette serve a small JavaScript application. An OpenAI-compatible client handles natural-language interpretation and read tools; a separate verifier extracts the customer's intent without seeing the draft. Fixed rules compare them and build the confirmation card. The execution gateway rechecks the signature, expiry, replay protection, allowance and guardian approval before calling PayPal.

PayPal is central: Payouts, Orders v2, capture refunds, Invoicing, disputes, transaction search and signature-verified webhooks. A record is saved before a request; keys derive from the signed card. Unknown responses are reconciled with the same keys. Status, amount and currency must match before success appears. Webhook deduplication and state changes persist together.

The keyless simulator runs the same client through an in-process transport adapter. It does not contact PayPal. Sandbox mode requires credentials and never silently falls back. Live money is unsupported.

## Challenges

Timeouts are harder than errors: a request can succeed while its reply is lost. Treating that as failure invites a duplicate payment. We kept unknown outcomes visible, reserved the allowance and reconciled before another attempt. Separately, a guardian approval must be tied to exactly the payment the customer sees, rather than to a general permission to spend.

## Accomplishments

The local demonstration covers signed transfers, purchase approval and capture, refunds, two-device guardian decisions and blocked invoice instructions. Automated tests exercise HTTP contract shapes, wrong signatures, wrong amounts and currencies, replay, restart and lost replies. Real-browser checks cover desktop, phone widths, Care mode, keyboard use, CSV export and receipt verification. Exact results are recorded in `docs/VERIFICATION.md`.

## What we learned

A reassuring model response is weak evidence for a financial action. A useful interface must also make uncertainty clear: “processing” and “unknown” cannot look like either success or a failed payment that is safe to retry.

## What's next

Run every documented path against actual sandbox accounts and an online model, resolve provider differences, and test with older adults. A production service would also need customer account permissions, external security review, reliable off-page family notifications and operational support.

## Built with

Python, Starlette, JavaScript, Web Crypto, WebAuthn, SQLite, requests, PayPal REST APIs, AG Grid Community, Playwright. Render blueprint supplied; deployment not yet verified.

## AI and sponsor tools

Claude Code and OpenAI Codex assisted with code, tests, review and materials under the team's direction. Runtime AI uses the configured OpenAI-compatible model. `[TEAM: name the model actually tested, its provider, and the test date]`. Offline rules are not an AI model. AG Grid Community powers the Activity & audit table. `[TEAM: disclose any other tools actually used]`.

## Links and testing instructions

- Source: https://github.com/FangHH916/paypal-ai-hackathon-agent — must be public with a confirmed open-source licence at submission.
- Demo: https://star-family-payments.onrender.com (keyless simulator and offline rules until the sandbox and model keys are set; the first visit may take about a minute to wake). Local run instructions are in the README.
- Video: `[TEAM: public YouTube URL, under three minutes]`.
- Team and rights: `[TEAM: confirm every contributor's permission and the earlier contest's reuse terms]`.

The project began as an earlier team voice-agent prototype. The PayPal rail, family-commerce product, current interface and AG Grid experience were built during this submission period. The README lists the retained components and substantive changes. No user adoption, fraud reduction or prize claim is made.
