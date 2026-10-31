# Local verification — 2026-11-01

Python 3.14.7 on Windows, offline model and PayPal simulator. No real payment credentials, model credentials or user data were used.

- `python -m unittest discover tests`: **489 passed**, zero failures or skips.
- `python qa/e2e_demo.py`: all checks passed in real Edge. Includes purchases, approval and capture, refunds, invoice stops, two-device guardian decisions, unknown-outcome recovery, AG Grid filter/export, offline receipt verification, phone widths and keyboard focus. No CSP violations or JavaScript errors were reported.
- Money regressions cover exact decimal parsing, wrong currencies, missing payout amounts, and crash/restart before a webhook commit. A failed commit leaves the webhook available for redelivery.

- Offline evaluation: 94/94 scripted cases passed; eight transaction-contract fault scenarios passed; all 31 lab attacks were blocked. These are fixed local examples, not a model benchmark or independent security audit.
- Gitleaks 8.30.1 scanned the full local history and working folder. No actionable credential was found. The history finding at `eae29f4:app/lab.py:239` is the constant `idempotency_key="attack"`; two working-folder findings are generated idempotency identifiers in ignored `eval/contract-report.json`. These are not service credentials. No broad scanner exclusions were added.
- `scripts/capture_demo.py --publish`: completed; 13 current screenshots and silent customer/guardian footage. Customer footage is approximately 161 seconds. The final competition video still needs narration and a public upload.

Fresh local clone of `a71418a`, with no ignored data or output files: 489/489 tests passed with no skips, and the full Edge browser run passed. No tracked file exceeds 5 MB. Hosted CI is a separate environment; local passing results do not establish a CI pass.

The first hosted browser run exposed a stale landing-copy assertion and Settings controls overflowing by 23 px with wider Linux font metrics. The assertion now matches the reviewed copy; the speech controls shrink and wrap within their parent. A bundled-Chromium probe reproduced the old overflow and blocked navigation with monospace, then passed with zero overflow in system-ui, Arial and monospace after the CSS fix. The browser suite includes that font-metric regression.

Unverified: actual PayPal sandbox API acceptance, online-model behaviour, hosted deployment, real microphone and mobile passkeys, and older-adult usability. No real-world fraud prevention or user-adoption claim is made. Silent local footage is draft material, not a final narrated competition video.
