# Before submitting: what only a person can do

Deadline: **Nov 12, 2026, 12:00 PT** (Nov 13, 04:00 Singapore time). Nothing below has been done yet. Delete this file when it's all done, if you prefer.

## Rights and rules

- [ ] **Licence and ownership.** `LICENSE` says MIT, "Star contributors". The contest requires the work to be the entrant's own. Get written agreement from every contributor in the history (ljjfh, TF916, JackieZhang2025, 傅涵), and from the author of the teammate's prototype the security lab was adapted from. Then confirm or change the copyright line, and who enters.
- [ ] **Prior work.** Star began as an entry to a different hackathon, and the same base is being used for another of the team's entries. Read each contest's rules on reuse and on submitting more than once. The "significant updates" section of the README is true as written; keep it that way.
- [ ] **Git history.** Earlier team code, assets and author metadata remain in history. History is preserved; confirm rights and reuse terms before making the repository public. Secret-scan evidence is in `docs/VERIFICATION.md`. A clean scan does not establish ownership or guarantee no secret exists.

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
- [ ] Make the repository public, fix the "Payal" typo in its description, and add collaborators. Check GitHub detects the licence.
- [ ] Open the National Elder Fraud Hotline link in `docs/IMPACT.md` and confirm the number. If `SCAM_HOTLINE_COUNTRY=SG` is ever shown, verify that number first; it's marked as not re-verified.
- [ ] As far as reasonably possible, check that the fictional merchants and people (`app/wallet/catalogue.py`, `app/wallet/ledger.py`) aren't real businesses or people.
- [ ] Fill in and submit the form.
