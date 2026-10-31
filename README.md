# Star: family payments

**A voice payment agent for seniors and their families, built on the PayPal sandbox. It can really pay, and it can't be talked into a scam.**

Star is a hackathon project with a fictional family. Built on the PayPal sandbox; not affiliated with PayPal.

Tell Star what you need in your own words: "Send fifty to my daughter", "Get me a blood pressure monitor", "What's this bill?", "I want my
money back". It finds the person, the offer or the invoice and prepares a card. You check the card, and your own device signs it.

The rule behind it: **the model understands, code decides, the customer authorizes.** The model has no tool that moves money. Every PayPal call that
moves money is reachable only through a deterministic kernel, a device-signed card and a zero-trust gateway.

## Try it in a minute (no keys)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.server:app --port 8000
```

Open http://localhost:8000 and press **Start as Mrs. Lee**. By default everything runs with no keys: a built-in PayPal simulator, an offline rule engine in place of the
AI model, and your browser's speech recognition. Everything simulated is labelled SIMULATED on screen, including every PayPal status.

| Try | What happens |
| --- | --- |
| Send 50 to my daughter | Card, simulated fingerprint, `POST /v1/payments/payouts`, and "Sent" only after a signature-checked event |
| Get me a blood pressure monitor | Three offers checked, one stopped. Sign, approve on a page labelled as a simulator, then capture and a status check |
| I want my money back | A refund of that capture, signed the same way |
| Bills → "Demo: a new invoice arrives", then "Pay the SecureTech bill" | Stopped by rules. The money-moving call counter stays at 0 |
| The police say I must move my savings to a safe account | Stopped, read aloud, with a fraud hotline |
| Family → Invite Anna, then "Pay Tom Reed 400 dollars" for a new person | The card waits for her signature on her own device |
| Security lab | Attacks, a model mistake frozen, a failure drill, and a scripted comparison |
| Activity & audit | Every payment and stop in an AG Grid table, with a hash-chained audit trail and signed receipts |

## What is real and what isn't

- **In simulator mode (the default), no call reaches PayPal.** The simulator follows the documented request and response shapes, but it is not PayPal.
- **In sandbox mode** (`PAYPAL_MODE=sandbox`), the same client calls `api-m.sandbox.paypal.com` with test money. It needs your own keys, and without them the app says so and refuses;
  it never falls back to the simulator. Live is not supported. `scripts/paypal_sandbox_check.py` exercises the real flow and prints pass or fail per API.
  See [docs/PAYPAL_NOTES.md](docs/PAYPAL_NOTES.md) for what has and hasn't been verified against the real sandbox.
- **The fingerprint is simulated by default**, and labelled. The signature behind it is real. Settings can switch to a real passkey.
- **Family alerts appear on the guardian page.** This demo sends no real texts or emails.

## Tests

```bash
pip install -r tests/requirements.txt
python -m unittest discover tests
python scripts/smoke_keyless.py http://localhost:8000      # against a running server, over HTTP

pip install -r qa/requirements.txt                         # the browser run: needs Edge, or set E2E_CHANNEL
python qa/e2e_demo.py                                      # starts its own keyless server and drives every story
```

## Layout

```
app/agent/     The model loop, prompts, independent verifier, scam judge. Drafts only.
app/kernel/    Rules, signing, passkeys, audit chain, protection level, the transaction contract.
app/paypal/    REST client, simulator, reconciliation, webhooks.
app/wallet/    The gateway, payment records, guardian, invoices, catalogue, agent tools.
web/           The pages. No build step, no third-party scripts, no inline code. AG Grid Community is vendored in web/vendor.
qa/            The real-browser test.
```

More: [docs/SECURITY.md](docs/SECURITY.md), [docs/RISK_CONTRACT.md](docs/RISK_CONTRACT.md), [CONTRIBUTING.md](CONTRIBUTING.md).
