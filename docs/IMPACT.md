# Impact: the problem, and what we don't claim

## The problem

Older adults lose more to reported online fraud than any other age group in the FBI's figures, and much of it happens the same way: someone persuades the victim to pay, and the victim's own login authorises it. Agents that can pay add a new route to this, as a scammer now only has to fool the software.

**FBI Internet Crime Complaint Center, [2024 IC3 Annual Report](https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf).** Fetched on 2026-11-01. Text extracted from the PDF; page numbers are the PDF's.

| Figure, for complainants aged 60 and over, 2024 | Value | Page |
| --- | --- | --- |
| Complaints | 147,127 (up 46% from 2023) | 27 |
| Reported losses | $4.885 billion (up 43% from 2023) | 27 |
| Average loss | $83,000 | 27 |
| Complainants who lost over $100,000 | 7,500 | 27 |
| Tech support losses | $982,440,006 | 29 |
| Confidence/romance losses | $389,312,356 | 29 |
| Government impersonation losses | $208,096,366 | 29 |

The same report's chart of complaints by age group, on page 8, shows the 60+ group with the highest complaints and the highest losses.

Caveats to keep with these numbers: they are complaints reported to the FBI, mostly from the US, not all complaints carry an age, and the report's Appendix C has its own data notes.

## How each demo flow answers it

| Scam type in the report | What Star does | Where |
| --- | --- | --- |
| Tech support | A fake "security renewal" invoice with a phone number in its note: warned, stopped by rules, and the family alerted | `app/kernel/trust.py` R80 to R84 |
| Government impersonation | "The police say I must move my savings to a safe account": stopped before any model runs | `app/agent/fraud.py` |
| Confidence/romance | A first payment to someone new above a threshold waits for the family guardian, and Star asks if it was her own idea | `app/kernel/contract.py`, `app/wallet/guardian.py` |

## What we don't claim

- **Star doesn't prevent these losses.** It works at the moment of payment, in a sandbox prototype.
- **We have no measured results.** Nobody outside the team has used it, and no older adult has tried it. There are no user numbers, savings figures or testimonials, because none exist.
- "Stops in this demo" in the Activity grid counts the stop records of that demo session, including any you trigger on purpose. It says nothing about the real world, and it deliberately isn't a sum of money: the same bill stopped three times isn't three bills.
- The fixed checks in the security lab show those behaviours. They aren't a penetration test or a model benchmark.

## Other sources

- **National Elder Fraud Hotline**, US Department of Justice, Office for Victims of Crime: [833-FRAUD-11 (833-372-8311)](https://ovc.ojp.gov/program/stop-elder-fraud/providing-help-restoring-hope). Fetched on 2026-11-01. This is the number Star offers when `SCAM_HOTLINE_COUNTRY=US`.

Who it is for comes from the hackathon's own brief, which names simpler experiences for seniors and families, and accessibility.
