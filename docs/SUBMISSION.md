# Star — Devpost submission draft

Replace every `[TEAM: ...]` before submitting. Say only what was seen: a sandbox payout to Anna's sandbox account was
seen arriving on the hosted demo on Nov 9, 2026 ("Sent, verified" after a PayPal status check, about 21 seconds after
signing). Local simulator footage is labelled SIMULATED and proves nothing about the sandbox.

## Project name

Star — the AI can draft the payment. Only she can sign it.

## Tagline

Voice payments to family for older adults, where a fooled AI still can't move a cent.

## Inspiration

Older adults lose more to fraud than any other group, and the scripts are the same every time: the police, the bank,
a grandchild in trouble, a virus on the computer. `[TEAM: one sourced figure, e.g. FBI IC3 Elder Fraud Report, with year]`.
Meanwhile payment agents are arriving that hand a language model tools such as `pay_order` and ask it to behave. A
model can be talked into things, exactly like a person can. We wanted the convenience of saying "send 50 to my
daughter" without giving the model, or anyone talking to her, a way to move her money.

## What it does

Star does one job: it sends money to her family when she asks in her own words, through PayPal Payouts.

1. Mrs. Lee (fictional, 74) says "Send 50 to my daughter". The model understands it and drafts a card. It has no tool
   that moves money.
2. Fixed rules check the draft against her own words: the person, the amount, someone new, limits, the time of night
   and the classic scam scripts. "The police say I must move my savings to a safe account" is stopped before any
   model sees it, with the fraud hotline, and 0 money-moving PayPal calls.
3. Her device signs the exact card she sees. Change one character and the signature no longer fits.
4. A separate gateway checks the signature again and calls PayPal Payouts, with the request id derived from the card.
5. "Sent, verified" appears only from a signature-checked PayPal webhook or a PayPal status check. A lost reply is
   reconciled, never retried with a new key.

Her daughter Anna is her family guardian: on her own phone she approves a first payment to someone new, refuses one,
or freezes everything at once. Lifting a freeze needs both of them.

The **Dashboard**, built with AG Studio, is the family's view: what she sent and to whom, whether PayPal confirmed each
payment and how, what Star stopped and which check stopped it. A custom widget, Safety path, shows every step a
request takes with live counts, and the stops branching off with their 0 PayPal calls. The AI assistant answers
questions about it and builds widgets ("Who got the most?").

## How we built it

- **PayPal:** Payouts (create, batch and item status, unclaimed-item cancel), signature-verified webhooks
  (`/v1/notifications/verify-webhook-signature`), OAuth client credentials, idempotent request ids, reconciliation of
  unknown outcomes. Orders, captures, refunds and Invoicing are implemented and tested too, but kept off the screen so
  the demo does one thing well. A Postman collection (`postman/`) replays the transfer path against the sandbox and
  shows Star refusing an unsigned card.
- **AI:** an OpenAI-compatible model with function calling drafts cards and answers questions; a second, independent
  reading of her words can freeze a draft; a separate scam assessment can only make things stricter. Offline rules
  run the same pipeline without a model and say so on screen.
- **Device signatures:** Web Crypto keys, or real passkeys (Touch ID, Face ID, Windows Hello), sign the card's digest.
- **AG Studio:** a React + TypeScript dashboard with three tables, a calendar, a currency format, a measure, a theme
  from the app's own colour tokens, a custom widget, and the Studio Agent Framework on Star's model through a
  Chat Completions adapter we wrote and a capped server proxy.
- **Stack:** Python, Starlette, vanilla JavaScript, SQLite, AG Grid, AG Studio, Vite, Playwright; deployed on Render.

## Challenges

- A payment request can succeed while its reply is lost. Treating that as a failure invites a second payment, so an
  unknown outcome stays visible, keeps its allowance reserved and is reconciled with the same request id.
- AG Studio writes inline styles whose values change with the data, while Star's page allows none. We run the
  dashboard in a same-origin frame with a policy of its own, so the app keeps its strict CSP.
- Our model speaks Chat Completions; AG Studio's example adapter speaks the Responses API. We wrote the adapter.

## Accomplishments

- A real sandbox payout from a spoken sentence, signed on the device and confirmed by PayPal, on the hosted demo.
- A planted 10x model bug ($500 drafted for her $50) is frozen by code and never reaches a card she can sign.
- `[TEAM: copy the current counts from docs/VERIFICATION.md: unit tests, browser checks, eval cases]`.

## What we learned

A reassuring model answer is weak evidence for moving money. The safe design is not a smarter model but a path where
the model's mistakes have nowhere to go, and where "processing" and "unknown" never look like success or like a
failure that is safe to retry.

## What's next

Test with older adults and their families; real notifications to the guardian; more languages; an external security
review before any live money.

## Built with

python, starlette, javascript, typescript, react, paypal-payouts, paypal-webhooks, paypal-rest-api, ag-studio,
ag-grid, webauthn, web-crypto, sqlite, vite, playwright, render, postman

## AI and sponsor tools

Claude Code and OpenAI Codex assisted with code, tests, review and materials under the team's direction. Runtime AI:
`[TEAM: the model behind LLM_MODEL on the hosted demo, its provider, and the date it was tested]`. Offline rules are
not an AI model. Sponsor tools: PayPal (Payouts, webhooks), AG Studio and AG Grid (Dashboard, Activity), Render
(hosting), Postman (collection in `postman/`).

## Links and testing instructions

- Source: https://github.com/FangHH916/paypal-ai-hackathon-agent (must be public, MIT licence on the front page).
- Demo: https://star-family-payments-agob.onrender.com (PayPal sandbox and an AI model). Steps for judges, with the
  sandbox test accounts, are under "For judges" in the README.
- Video: `[TEAM: public YouTube URL, under three minutes]`.
- Team and rights: `[TEAM: confirm every contributor's permission and the earlier contest's reuse terms]`.

The project began as an earlier team voice-agent prototype. The PayPal rail, the family product, the interface, the
Dashboard and the AG Grid work were built during this submission period; the README lists what was kept and what
changed. No user adoption, fraud reduction or prize claim is made.
