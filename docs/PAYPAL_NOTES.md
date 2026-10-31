# PayPal integration notes

API by API: what Star uses, where the shape comes from, what has and hasn't been verified, and how the simulator differs from
the real thing.

**Status today: no call in this repository has reached the real PayPal sandbox.** No credentials were available when this was
written. Request and simulator-response shapes are checked by `tests/test_paypal_spec.py` against PayPal's official OpenAPI
files (`github.com/paypal/paypal-rest-api-specifications`, Apache-2.0; a subset is in `tests/fixtures/paypal/`). That catches
wrong field names, not wrong behaviour. Run `scripts/paypal_sandbox_check.py` with real keys, and update this file with what it
prints. Everything marked **unverified** below is what that run is for.

## Roles

| Role | Sandbox account | Does |
| --- | --- | --- |
| Household and demo merchants | Business, owns the REST app | Payouts sender, Orders merchant, Invoice issuer, Refund caller |
| Mrs. Lee | Personal | Approves orders, pays invoices |
| Family | Personal, `PAYPAL_SANDBOX_RECEIVERS` JSON keyed by payee id | Receive payouts |

Stated limitation: a production version would connect each customer's own account through partner permissions, which this entry
does not build. All demo merchants settle into the one business account, and their names and trust data are demo content.
Star can't read a PayPal balance: it enforces an allowance the family sets.

## APIs

| API | Calls | Idempotency | Source | Unverified |
| --- | --- | --- | --- | --- |
| OAuth2 | `POST /v1/oauth2/token` | none | docs | |
| Payouts | `POST /v1/payments/payouts`, `GET .../payouts/{id}`, `GET .../payouts-item/{id}`, `POST .../payouts-item/{id}/cancel` | `sender_batch_id` = `star-` + digest[:40], also `PayPal-Request-Id`; `sender_item_id` = digest[:32] | spec | The exact JSON of the duplicate-id rejection. The spec promises a HATEOAS link; `batch_id_from_links` reads `links[]` generically. The check script prints the real one |
| Orders v2 | `POST /v2/checkout/orders`, `GET .../{id}`, `POST .../{id}/capture` | `ord-` + digest[:40], `cap-` + digest[:40]; the plan read the key's storage as 6 hours from the docs | spec and docs | Whether `custom_id` comes back on the capture in every case; sandbox approval page behaviour with `NO_SHIPPING` |
| Payments v2 | `POST /v2/payments/captures/{id}/refund`, `GET /v2/payments/captures/{id}`, `GET /v2/payments/refunds/{id}` | `ref-` + digest[:40] | spec | |
| Invoicing v2 | `POST /v2/invoicing/invoices`, `POST .../{id}/send`, `GET .../{id}`, `POST /v2/invoicing/search-invoices` | No header in the spec, so `detail.invoice_number` = `STAR-` + digest[:16], and reconcile searches by it before any retry | spec | Whether the sandbox honours `invoicer.business_name` different from the account (the seed script relies on it for demo billers); search by `reference` |
| Disputes | `GET /v1/customer/disputes`, `GET .../{id}` | read only | spec | |
| Reporting | `GET /v1/reporting/transactions` | read only | spec | Reporting delay. Star's own verified records are the primary source; this call is in the reader and the check script, not used for status |
| Webhooks | `POST /v1/notifications/verify-webhook-signature`, `GET /v1/notifications/webhooks` | dedup by event id | spec, event names page | The raw event is spliced in instead of re-serialised: re-serialising is widely reported to break verification, which we have not confirmed. Resource shapes of payout and invoice events: ids are read from every documented place. Whether `simulate-event` events verify |

Error codes the code branches on, all seen on PayPal's Orders errors page: `ORDER_NOT_APPROVED`, `ORDER_ALREADY_CAPTURED`,
`INSTRUMENT_DECLINED`. Everything else is treated generically by HTTP status.

**Not used, on purpose.** `POST /v2/invoicing/invoices/{id}/payments` records a payment from the invoicer's side and would fake one.
`paypal-agent-toolkit` hands a model tools such as `pay_order`, which is a reasonable choice for other products and the exact pattern
Star avoids: here the model has no tool that moves money. JS SDK: full-page redirect to the approval link keeps the CSP free of
third-party scripts. Live mode: there is no live base URL in the code, and `PAYPAL_MODE=live` is refused.

## Failure semantics

See `app/paypal/reconcile.py` for the matrix. In short: a record is saved before the HTTP call; a timeout, reset or 5xx is UNKNOWN,
never a failure and never a new key; `R60_UNKNOWN_OUTCOME` blocks paying again; "paid" comes only from a GET or a webhook PayPal itself
verified; an amount that disagrees with the signed card is an ANOMALY and a family alert.

## The simulator

`app/paypal/simulator.py` is a `requests` transport adapter on the same session and client code as the sandbox. Base URL
`https://paypal-simulator.invalid`, which can't resolve. Everything it produces is labelled SIMULATED in the UI. It moves no money.

| Behaviour | Simulator | Real PayPal |
| --- | --- | --- |
| Webhook signature | HMAC under a process-local secret, algorithm `SIMHMACSHA256` | Certificate-based |
| Webhook delivery | Queued, then handed to the same handler when the app polls | HTTP POST to your public URL |
| Approval and invoice pages | `/simulator/checkout`, `/simulator/invoice` on this server, plainly marked | PayPal's own pages |
| Who pays | A button on that page | A logged-in sandbox buyer |
| Payout outcome | `@family.example` receivers succeed after `PAYPAL_SIM_SETTLE_SECONDS`; anyone else stays UNCLAIMED | Depends on the receiver account |
| Duplicate batch error | `400 USER_BUSINESS_ERROR` with a `self` link: our guess at the undocumented shape | To be confirmed |
| Invoices | Seeded per visitor under a reference tag | Issued by `scripts/paypal_seed_sandbox.py`, tag `star-demo` |
| Disputes | One fixed demo case | Whatever the account has |
| Refund limit error | Generic `422 UNPROCESSABLE_ENTITY` | A specific code we don't branch on |
| Faults | `timeout_after_commit`, `timeout_before_commit`, `http_500`, `decline_instrument`, `delayed_webhook`, `duplicate_webhook`, `forged_webhook`, `amount_mismatch`, `capture_pending` | None |

## Verifying against the sandbox

1. In the developer dashboard create a REST app, one business and at least two personal sandbox accounts.
2. Register a webhook for the events in `app/paypal/webhooks.py`, pointing at `{your host}/api/paypal/webhook`. Note its id.
3. Run, with the secrets in your environment and never in a file you commit:
   `PAYPAL_MODE=sandbox PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=... PAYPAL_WEBHOOK_ID=... python scripts/paypal_sandbox_check.py --receiver <personal> --unclaimed <address with no account> --buyer <personal>`
4. `python scripts/paypal_seed_sandbox.py <personal>` issues the demo's invoices.
5. Copy what you learned into this file. Fix what differs.
