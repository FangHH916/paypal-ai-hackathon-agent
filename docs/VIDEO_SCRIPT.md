# Demo video script (2:50)

Record on the hosted sandbox demo, 1920x1080, browser zoom 100%. Two windows: Mrs. Lee (normal window) and Anna (a
private window on the guardian page, paired beforehand). Keep Agent trace closed unless a shot says otherwise.
Speak slowly; every claim below is something on screen. Judges stop at 3:00, so the whole story is in the first 2:50.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:15 | A scam call transcript on a black card: "The police say I must move my savings to a safe account." | "Older adults lose more to fraud than anyone. And now AI agents are getting the power to pay. What happens when someone talks the AI into it?" |
| 0:15–0:25 | Star Home, Mrs. Lee. | "This is Star. Mrs. Lee is 74. She sends money to her family by just saying so, through PayPal." |
| 0:25–0:55 | Talk to Star: type or say "Send 50 to my daughter". The card appears (SANDBOX tag). Sign with the fingerprint. Tracker: PayPal is processing, then Sent, verified. | "The AI understands her and drafts a card. But it has no tool that moves money. She checks the card and signs it on her own device. Star calls PayPal Payouts, and says 'sent' only when PayPal confirms it." |
| 0:55–1:00 | Anna's sandbox PayPal account, the $50 payment received. | "It really arrived, in the PayPal sandbox." |
| 1:00–1:25 | Say "The police say I must move my savings to a safe account". The stop screen: hotline, 0 money-moving PayPal calls. | "Now the scam. Star stops it before any model even sees the sentence, gives her the fraud hotline, and nothing reaches PayPal." |
| 1:25–1:50 | Open `/#/lab`, run the model mistake: the model drafts $500 for her $50; the draft is frozen, never shown to sign. | "What if the AI itself goes wrong? We planted a bug that writes ten times the amount. Code re-reads her words, sees $50, and freezes it. A fooled model produces, at worst, a frozen draft." |
| 1:50–2:10 | Say "Add a new person Tom Reed, tom.reed@family.example", then "Pay Tom Reed 400 dollars". Anna's window: approve or refuse. | "Someone new? Her daughter Anna decides, on her own phone. And either of them can freeze everything at once." |
| 2:10–2:40 | Dashboard: Her money, the Safety path widget, then What Star stopped. Edit dashboard, ask the AI assistant "Who got the most?". | "For the family, a dashboard built with AG Studio: what she sent, whether PayPal confirmed it, and every stop with zero PayPal calls. They can just ask it questions." |
| 2:40–2:50 | Star logo, the line "The AI can draft the payment. Only she can sign it.", the repo URL. | "Star. The AI can draft the payment. Only she can sign it." |

Checklist before recording:

- [ ] The hosted demo is awake (open it a minute before) and shows SANDBOX, not SIMULATED.
- [ ] `AG_STUDIO_LICENSE` is set on the host, so the dashboard has no watermark.
- [ ] The model is connected (the header names it), so the assistant appears in Edit dashboard.
- [ ] Anna is paired in the private window before recording the new-person shot.
- [ ] Upload to YouTube as Public or Unlisted, under 3:00, and paste the URL into `docs/SUBMISSION.md`.
