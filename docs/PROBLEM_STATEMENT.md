# Problem statement

## The gap
A company with 200 to 2,000 people usually has an IT manager, maybe two security people, and the same attackers
as a bank. Its tools produce thousands of alerts a day; most are noise, and the few that matter arrive at 3 a.m.
Round-the-clock security operations are out of reach: keeping one analyst seat filled around the clock takes
roughly five people, before any tooling.

At the same time, the reporting clock has become law in every region such a company is likely to operate in:

| Regime | Who | Deadline |
|---|---|---|
| **EU NIS2** (Directive (EU) 2022/2555, Art. 23; transposition deadline 17 October 2024) | Essential and important entities in 18 sectors, including transport and postal/courier services | Early warning within **24 hours** of becoming aware of a significant incident; notification within **72 hours**; final report within **one month** |
| **US SEC Form 8-K Item 1.05** (applies since 18 December 2023) | Public companies | Disclose a material cybersecurity incident within **four business days** of determining it is material |
| **Australia SOCI Act 2018, Part 2B** | Responsible entities for critical infrastructure assets, including critical freight services | **12 hours** for an incident with a significant impact on availability; **72 hours** for other impacts |
| **EU GDPR Art. 33** | Any controller of personal data | Notify the supervisory authority of a personal data breach within **72 hours** of becoming aware, where feasible |

A mid-size logistics group with a Belgian subsidiary, a US listing and an Australian freight network is subject
to all three, each with its own trigger, clock and recipient, and to GDPR whenever personal data is involved. Missing the early warning because nobody connected
"unusual sign-in" to "significant incident" is a compliance failure on top of the breach.

## Who we serve
- **IT managers and small security teams** need the night shift done for them: alerts grouped into incidents,
  noise suppressed, every real incident investigated with the evidence laid out, and containment steps ready to
  approve.
- **Incident responders** need to act fast without handing a machine the keys: nothing disruptive happens until
  a person authorises it.
- **CISOs and executives** need to know when a regulator clock is running, what is due when, and a plain weekly
  account of posture and response times.
- **Auditors** need a tamper-evident record of who (person or AI colleague) did what, and why.

## What Nightjar SOC does
1. **Takes in events** continuously (webhooks, syslog-style JSON, file replay) and normalises them to one schema.
2. **Detects and groups**: open Sigma rules mapped to MITRE ATT&CK, plus behaviour baselines per person and
   machine (Isolation Forest), grouped by time and shared entities into incidents.
3. **Separates real from noise**: a calibrated gradient-boosted classifier scores every alert with plain-language
   reasons, so a backup-software update that fires 146 alerts becomes one benign incident, not a night of pages.
4. **Investigates with cooperating AI colleagues**: a triage colleague decides real or noise with evidence, an
   enrichment colleague checks indicators against threat intel and known-exploited vulnerabilities, and a response
   colleague proposes containment. Every step is recorded and replayable.
5. **Keeps humans in charge**: isolating a server or disabling an account needs a responder's approval, and a
   crown-jewel system needs the CISO as well.
6. **Starts the regulator's clock**: proposes which regimes apply (NIS2, SEC Form 8-K Item 1.05, SOCI, GDPR
   Art. 33) from the company profile and the incident facts, lets the CISO decide, counts down each deadline
   (business days for the SEC) and drafts the early warning, notification and final report. Submissions are
   recorded, not sent.
7. **Supports hunting and reporting**: saved searches over the event store with suggested hunts, an executive
   posture view, a CISO morning brief and a weekly report.

## Success measures (evaluated in `eval/`, gated in CI)
- Detection recall and post-triage precision on labelled synthetic replays (OTRF scenarios can be replayed through
  the same pipeline, but carry no gate).
- Behaviour-anomaly precision@k and recall against injected anomalies.
- Triage classifier PR-AUC, calibration, and false-positive reduction versus rules alone.
- Triage-colleague verdict accuracy against labels; alert-to-triage under **60 seconds** at demo scale.
- Safety: zero destructive actions without approval; prompt-injection text in log fields is neutralised.

## Out of scope
Real endpoint or identity-provider control (containment is simulated against the inventory), real regulator
filings (submissions are recorded, mocked), legal advice on whether an incident is significant or material (the
product supports the decision; a person makes it), and certification claims of any kind.
