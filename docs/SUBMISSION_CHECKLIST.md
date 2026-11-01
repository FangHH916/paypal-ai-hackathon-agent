# Before submitting: what only a person can do

Deadline: **Nov 12, 2026, 12:00 PT** (Nov 13, 04:00 Singapore time). Nothing below has been done yet. Delete this file when it's all done, if you prefer.

## Rights and rules

- [x] **Licence and ownership.** The three team members built Star together and agreed to adapt it for this entry (confirmed by the team on Nov 1, 2026). `LICENSE` says MIT, "Star contributors": confirm the copyright line and who enters.
- [x] **Prior work.** The team confirmed the rules allow this reuse. Star began as an entry to a different hackathon, and the README's "significant updates" section says so: keep it true and keep it in the submission.
- [x] **Git history.** Rewritten on Nov 1, 2026 to remove the earlier project's branding, assets and generated QA output. Commits, authors and dates are kept. A clean scan does not guarantee no secret exists.

## Make it real

- [ ] **PayPal sandbox.** Follow "Get sandbox keys" in the README and run `scripts/paypal_sandbox_check.py`. Leave days to fix what it finds. Record the results in `docs/PAYPAL_NOTES.md`, and change the README's "Ever run" row and the Limitations section to what is then true. Never commit the keys.
- [ ] **AI model.** Choose an OpenAI-compatible model with function calling, try the demo sentences with it, and name it in the README and `docs/SUBMISSION.md`. **The keyless path has no AI.** Judges on a keyless URL see the rule engine, in a contest that requires AI, so the hosted demo should have a key (set a spending limit at the provider; the app rate-limits per device).
- [ ] **Render.** Connect the repository, deploy the blueprint, set the secrets and a paid plan for judging week. Put the URL in the README and `docs/SUBMISSION.md`. Check that the footer, the SIMULATED or SANDBOX tags and the guardian link all work on it.
- [ ] **Sponsors.** Check the current terms for the AG Grid and Render prizes.

## Video

- [ ] Follow `docs/DEMO_VIDEO.md`: record on the sandbox with a real model if possible, narrate, keep it under 3:00, and upload to public YouTube.
- [ ] State on screen what is simulated. Check there are no third-party logos, no music without permission, and no real names, keys or notifications in the shot.

## Final checks

- [ ] Fill in every **[TEAM: ...]** in `docs/SUBMISSION.md`, and confirm "Other AI tools used".
- [ ] Make the repository public and add the intended collaborators. The PayPal description typo has been corrected. Check GitHub detects the licence.
- [ ] Open the National Elder Fraud Hotline link in `docs/IMPACT.md` and confirm the number.
- [ ] As far as reasonably possible, check that the fictional merchants and people (`app/wallet/catalogue.py`, `app/wallet/ledger.py`) aren't real businesses or people.
- [ ] Fill in and submit the form.
