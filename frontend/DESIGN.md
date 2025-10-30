# Nightjar SOC frontend design: the night watch

> Copy this file to `~/Work/nightjar-soc/frontend/DESIGN.md`. The static prototype lives in `prototype/` next to this file. Open `index.html`, `console.html` or `brief.html` directly. Add `?theme=light|dark`, `?still` for the settled, reduced-motion state, `?palette` to open ⌘K, or `?holding` to freeze the hold at 64%. Screenshots are in `shots/`. Build to this document. The prototype is the visual reference for type, colour, spacing, the baton grammar and the clock bar.

Nightjar SOC is a managed security-operations platform for mid-size companies. It takes in security events around the clock and groups threats into incidents (Sigma rules, MITRE ATT&CK). It spots unusual behaviour, and three cooperating AI teammates (**Triage**, **Enrichment**, **Response**) investigate. When an incident is reportable, it starts the regulator's clock (EU NIS2, US SEC Form 8-K Item 1.05, Australia SOCI) and drafts the notice. **A person approves every response action.** Executives get a plain-language morning brief.

- **Demo company:** **Halden Logistics**, with an EU hub in Antwerp, offices in Rotterdam and Amsterdam, US-listed shares with an office in New Jersey, and freight services in Melbourne.
- **Stack:** SvelteKit + TypeScript + Tailwind 4.1 (the tokens below go into `@theme`). Motion uses Svelte built-ins only: `svelte/transition`, `svelte/animate` (`flip`), `crossfade`, and the `Spring`/`Tween` classes from `svelte/motion` with `prefersReducedMotion`. There is no animation library.
- **Copy date:** copy is written as of **late November 2025**. Anything time-sensitive is computed at runtime (§13).

---

## 1. References

Every reference was re-checked in this design round. Vendor marketing pages were used only where documentation was thin, and the table says so.

| # | Reference | What we take |
|---|---|---|
| 1 | Google SecOps (Chronicle) case graph and playback: https://cloud.google.com/chronicle/docs/soar/investigate/working-with-cases/explore-entities-and-alerts-investigation | A **play control that steps through a case's alerts in order**, highlighting what each step touched and dimming the rest. → Our **Replay**: the baton timeline steps through the teammates' work. The current step is lit and future steps sit at 35%. We use no graph canvas (Kinroot owns graphs). |
| 2 | Microsoft Defender / Sentinel attack story: https://learn.microsoft.com/en-us/defender-xdr/investigate-incidents | **One queue and one attack story** per incident instead of separate alerts, with alerts replayed in time order. → Alerts coalesce into one incident strip, and the incident page tells one story from top to bottom. |
| 3 | Datadog Bits Security Analyst: https://docs.datadoghq.com/bits_ai/bits_security_analyst and signal investigation: https://docs.datadoghq.com/security/cloud_siem/triage_and_investigate/investigate_security_signals/ | An automated investigation ends in a **plain verdict with the evidence underneath**, an "Investigating" state until then, and next actions (declare an incident, suppress) on the same panel. → The Triage step shows "Likely real: 94 in 100", then "why …" and "looked at …". |
| 4 | CrowdStrike Charlotte AI agentic SOC (press, April 2025): https://www.businesswire.com/news/home/20250428067495/en | **Bounded autonomy**: the customer decides what runs on its own and what waits for analyst approval. → In Nightjar SOC nothing destructive runs on its own. Every response action is gated, and the gate is a visible, physical gesture (Hold). |
| 5 | Torq Socrates / HyperSOC (vendor): https://torq.io/blog/socrates | An orchestrator with specialised agents and an **auditable record of reasoning**, available from the case. → Teammates hand off to each other, and every step records what it looked at and why it decided. |
| 6 | SentinelOne Purple AI (vendor): https://www.sentinelone.com/platform/purple/ | **Storyline** gathers atomic events into one narrative. **Quick Starts** are pre-written hunting questions, and **notebooks** keep hunts as reusable work. → Plain-language hunting suggestions and saved searches kept as named, shareable hunts. |
| 7 | Wiz Defend incident timeline (vendor): https://www.wiz.io/blog/wiz-defend-general-availability and a third-party UX teardown: https://sv0-docs.pages.dev/docs/architecture/research/2026-04-03-wiz-cloud-demo-ux-analysis | Colour encodes **what kind of thing** something is, not severity. A **Table/Graph toggle** sits over the same query. → Colour marks the role (teal = teammate, lavender = a person is needed); severity is a bar glyph. Hunting has a **List / Timeline** toggle over one search. |
| 8 | Panther detections and simple builder: https://docs.panther.com/detections/rules/simple-detection-builder | Detections-as-code with a form view that keeps the code's testability, and search across events without writing a query language. → The rules view shows each rule as a **readable sentence** (form) with the Sigma source one keystroke away, and tests on replayed events. |
| 9 | Tines Workbench, May 2025 changes: https://www.tines.com/blog/what-s-new-in-tines-may-2025-edition/ | A case status change **previews unmet requirements** before you commit, plus a cleaner case activity timeline. → "Submit notice" first lists any required field still missing (◐) and links each one to its evidence. |
| 10 | Splunk ES Mission Control response plans: https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.7/mission-control/respond-to-investigations-with-response-plans-in-splunk-enterprise-security | Phased response plans (NIST 800-61 style) with tasks per phase. → The **stage rail** on each incident: Triage → Investigate → Contain → Report, with the waiting item named. |
| 11 | incident.io timeline curation: https://incident.io/changelog/tell-the-full-story-with-your-incident-timeline | The default timeline holds only meaningful updates, the full activity sits behind "View all activity", and timeline entries are editable. → The replay shows **key steps**, and "all activity" is a toggle. The curated replay becomes the post-incident report. |
| 12 | Grafana IRM incident timeline: https://grafana.com/docs/grafana-cloud/alerting-and-irm/irm/manage-incidents/incident-timeline/ | The timeline is the incident record and turns into a post-incident review document. → The weekly report is assembled from curated replays, not typed from scratch. |
| 13 | PagerDuty Status Dashboard: https://support.pagerduty.com/docs/status-dashboard | Stakeholders see **business services** as impacted or not, not technical noise. → The brief speaks in business systems ("the Melbourne booking system", "payments"), never host names first. |
| 14 | Warp blocks: https://docs.warp.dev/terminal/appearance/blocks-behavior | Output grouped into discrete blocks with dividers and a compact mode. → The event river is grouped **per minute**, with routine events folded into a dashed "+1,257 routine events folded" block. |
| 15 | Linear contextual command menu: https://linear.app/changelog/2019-10-07-contextual-command-menu, and cmdk (Linear/Vercel/Raycast pattern): https://github.com/pacocoursey/cmdk | A **context-aware ⌘K** as the primary navigation, with results ranked by the current view and grouped (do / go to / switch). → ⌘K is scoped to the open incident first (`INC-0412` chip), and Tab widens it to everything. |
| 16 | Raycast's visual language (third-party extraction): https://design.hagicode.com/designs/raycast/ | A near-black canvas, **monospaced shortcut keycaps** and a single accent for selection. → Keycaps with a 2 px bottom border, and the selected palette row in a teal wash. |
| 17 | Vercel Geist: https://vercel.com/geist/font and https://vercel.com/design/geist | Geist + Geist Mono, high-contrast greys and tabular figures for numbers. → Clocks and IDs in Geist Mono with `tnum`; UI and reading text in Geist. |
| 18 | Linear Pulse (daily/weekly summary, audio): https://linear.app/changelog/2025-04-16-pulse | A personal **digest** delivered daily or weekly that can be read or listened to. → The morning brief arrives at 08:00 local time, and the weekly report on Monday. A "listen" option is a later idea. |
| 19 | The Economist Espresso (finishable daily briefing): https://www.campaignasia.com/article/with-espresso-economist-delivers-daily-dose-of-intelligence/391888 | A brief you can **finish**, readable without clicking anything. → The CISO brief is one column with a "2-minute read" dateline. Every fact needed for the decision is on the page. |
| 20 | Hold-to-confirm pattern: https://motion.dev/tutorials/js-hold-to-confirm, and why holding beats dialogs: https://dev.to/tomj/why-holding-buttons-is-superior-to-confirmation-dialogs-in-ux-design-1fic | Holding avoids confirmation fatigue. Announce "press and hold" to screen readers, and releasing early cancels. → **Hold to authorise**, 1.2 s, with pointer, Space or Enter (§11). |
| 21 | NASA Open MCT: https://nasa.github.io/openmct/about-open-mct | Composable tiled telemetry layouts and the calm density of mission control. → Tiles in the style of tmux with hairline gutters and an active-tile top rule, not cards. |
| 22 | Hyperping status page refresh: https://hyperping.com/blog/product-update-status-page-calendar-and-new-design | A **calm grey scale in both themes** so the one accent and the incidents stand out. → Teal is rationed to live, focus, the teammates and "on track". |
| 23 | Gallery pieces with console, ops or night themes: Awwwards "Darknode" (SOTD, stealth/mission minimalism) https://www.awwwards.com/sites/darknode · Awwwards mission-control inspiration https://www.awwwards.com/inspiration/desktop-mission-control-1 · Order's interactive-console launch page (One Page Love) https://onepagelove.com/?p=49097 · "Orbital Reach" flight-director terminal landing (Kombai gallery) https://kombai.com/gallery/inspirations/orbital/ | A landing that **is** a working console rather than a picture of one, with minimal chrome and a single live element carrying the story. → The landing is the console replaying a night, and the headline arrives as the last log entry. **Avoided:** neon on black, glowing world maps (the Dribbble "security dashboard" cliché), scanlines and fake-hacker green. *Note: godly.website now redirects to recent.design; this was checked, and no Godly entry matched better than the pieces above.* |
| 24 | Svelte motion API: https://svelte.dev/docs/svelte/svelte-motion | The `Spring`/`Tween` classes (`Tween.of`, `preserveMomentum`) and `prefersReducedMotion`. These drive Tick, Drop and Hold, plus `crossfade` + `flip` for Coalesce. |

---

## 2. Metaphor and mental model

**The night watch.** A mission-control floor at night: someone is always on watch, and the watch keeps a log. The user thinks in **time**:

- **What is happening now?** The event river.
- **Which clock is running?** The regulator clocks, always in the top bar.
- **What needs me?** "Awaiting you".
- **What happened while I was away?** The replay and the morning brief.

Incidents are **operations with countdowns**. The three AI teammates are colleagues on shift (**Triage**, **Enrichment**, **Response**) who pass the work along like a **baton**. You can replay every hand-off and see what each teammate looked at and why it decided. People sit in their own lane: the baton always stops at the **people** lane before anything is switched off.

Executives don't get the floor in white. They get **the morning after**: a calm, single-column brief written as a hand-over note at first light (§6.11). Night is the analyst's console; dawn is the CISO's brief. The two themes are two times of day, not two colour schemes of one screen.

### Primary workflows by role

| Role | Comes to Nightjar SOC to… | Main path |
|---|---|---|
| **Analyst** (security analyst, often the IT manager in a mid-size company) | confirm or overturn the teammates' verdicts, hunt for what else was touched, and tune noisy rules | The watch (river · incident · hunt layout) → incident replay → "Hunt: laptops that opened the same link" → save the search → rules: lower the noise on a rule |
| **Responder** (on call) | authorise or decline response actions, fast, with the reasons in front of them | Notification → the watch (river · incident · awaiting you) → read *does / why / undo* → **Hold to authorise** → next item |
| **CISO / executive** | know whether anything is reportable, which clocks run, and what decision is theirs | Morning brief (light) → "Your decision" → review the draft notice → submit (mock) → weekly report |
| **Auditor** (read only) | check that every response action had a person behind it, every notice went out on time, and the log is intact | Activity log → "Verify the log" → open any incident's replay at any step → export the evidence pack |

---

## 3. Navigation model and app shell

**⌘K-first tiled console. No sidebar, no tabs, no menus.** Three pieces of chrome:

```
┌─────────────┬──────────────────────┬──────────────────────┬─────────────────────┬──────────────────────┬────────────────┐
│ ◉ nightjar  │ NIS2 · BE · early    │ NIS2 · BE · full     │ SOCI · AU · 72 h    │ SEC 8-K · US         │ Go to anything │  ← CLOCK BAR 56 px
│   soc       │ 17:53:38 ○ Draft ready│ 65:53:38 ○ On track │ 61:21:34 ○ On track │ not started ◆ decide │ ⌘ K   ☼        │    one cell per running clock
├─────────────┴──────────────────────┴──────────────────────┴─────────────────────┴──────────────────────┴────────────────┤    2 px elapsed rule under each cell
│                                                                                                                          │
│   TILES: 1–4 panes with 1 px hairline gutters, no radius, active pane = 2 px teal top rule                               │
│                                                                                                                          │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ● Taking in events normally │ Triage · Enrichment · Response on shift │ Priya Nair · responder │ layout ⌘[ ] │ ⌘K go anywhere │  ← STATUS LINE 28 px, mono
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Clock bar (top, always).** One cell per running regulator clock, across all open reportable incidents, ordered by time left. Each cell has a label (regime · country · stage), rolling digits and a status. Clicking a cell opens the notice draft beside its evidence (§6.6). The SEC cell exists before its clock starts, with the label "not started · ◆ Materiality decision". With no clocks running, the bar reads "No regulator clocks running" in a quiet tone.
- **Tiles.** The workspace splits into 1 to 4 panes in the style of tmux. Keys: `⌘\` splits, `⌘[` / `⌘]` move focus, `⌘W` closes, `⌘⇧↵` zooms one tile. Each pane header is a lowercase mono title ("events", "incident", "awaiting you", "hunt"), and the title is itself a ⌘K target: click it to swap what the tile shows.
- **Layouts per role** are presets saved per person:
  - Analyst: events | incident | hunt
  - Responder: events | incident | awaiting you
  - Auditor: activity | incident (read only) | evidence
  - Executive: lands on the brief instead of tiles.
- **Status line (bottom).** Health in plain words ("Taking in events normally", or "Catching up: 4 minutes behind" in amber), who is on shift, your name and role, the layout and the ⌘K hint. It never shows engineering metrics.
- **⌘K / Ctrl K** is the navigation. Groups: **do** (approve, decline, isolate, release, submit notice), **go to** (incidents, hunts, saved searches, assets, people, clocks, rules, settings, activity) and **switch** (layout, theme, morning brief `⌘B`). It is scoped to the focused incident first (an `INC-0412` chip); Tab widens it to everything. Typing `?` asks the watch a question in plain language ("which laptops opened the link?"), and the answer comes back as a hunt tile. Clicking the brand mark opens ⌘K in "go to" mode for mouse-only users. `g` chords also work: `g w` the watch, `g h` hunt, `g b` brief, `g r` rules, `g a` assets, `g l` activity log, `g s` settings.
- **Routes** (deep links, all rendering inside the shell):
  - `/` landing · `/sign-in`
  - `/watch` (home) · `/incidents/[id]` · `/incidents/[id]/notice/[regime]`
  - `/hunt` · `/hunt/saved` · `/behaviour` · `/rules` · `/rules/[id]`
  - `/assets` · `/identities`
  - `/brief` · `/brief/week/[isoWeek]`
  - `/settings/regimes` · `/settings/members` · `/activity`
- **Mobile (390 px).** One tile at a time; swiping or the tile dots switch tiles. The clock bar collapses to a sticky chip showing the most urgent clock ("NIS2 17:53 · +2"); tapping it expands a sheet. ⌘K becomes a floating search button at the bottom right. Hold to authorise is full-width and gives a 10 ms vibration when it completes.

---

## 4. Landing story (public, `/`): "Replay the night"

The landing **is the console**, replaying one night at Halden Logistics. It is time-driven, not scroll-driven. There is no hero image, no product card and no stat strip. It has three columns, like the app:

- **Left, "events":** the river streams routine events in plain words, and the three suspicious rows appear with ◇ in amber.
- **Centre, the night log:**
  - a mono eyebrow ("● replay · Halden Logistics · the night of Wednesday to Thursday");
  - a large rolling clock (Geist Mono 250 weight, 112 px, seconds in teal);
  - below it, **log lines** that each arrive large (24 px) and settle into the log (16 px) when the next one comes.
- **Right, "incident":** "Nothing needs a person. The watch is quiet." until the alerts **coalesce** into an incident strip. Inside the strip a mini baton graph grows (T → E → R → P), then the **Hold to authorise** button appears. Visitors can hold it themselves; otherwise "Priya, on call" does at 15 s.

**Beats** (about 16 s, replay clock at 6×; the virtual night starts at 03:11:50):

| t | Beat | Log line |
|---|---|---|
| 0 s | river streams routine events | "It is quiet at Halden Logistics. Events arrive by the thousand, nearly all of them routine." |
| 1.7 s | ◇ new-country sign-in | "Marta in accounts payable signs in from a country she has never signed in from." |
| 3.0–3.6 s | ◇ inbox rule, ◇ password reset | (river only) |
| 4.8 s | **Coalesce** into INC-0412 | "Three alerts become one incident." |
| 6.2–8.4 s | **Baton** T → E → R | "Triage says it is likely real and shows why. Enrichment checks who is behind it. Response drafts a plan." |
| 9.8 s | **Drop**: two NIS2 clock cells drop into the top bar and start counting down from 24:00:00 and 72:00:00 | "Reportable under NIS2. The 24-hour clock starts, and the early warning is already drafted." |
| 12 s | the Hold button appears; replay slows to 2× | "Disable Marta's account? Nothing happens until a person holds to authorise." |
| 15.2 s | auto-hold if the visitor hasn't held | "Priya, on call, held to authorise. Account disabled, two minutes after the first sign." |
| 17.8 s | **Final**: the clock shrinks to 64 px and turns muted, the log lines settle, a rule appears and the headline lands | — |

**Final state** (the shot): the centre column reads as a finished night log, then the headline **"Someone is always / on watch."**. The sub-line follows: *"Nightjar SOC watches your company's security through the night, separates real threats from noise, investigates with three AI teammates, starts the regulator's clock and drafts the notice. Nothing is switched off or locked out until a person says so."* The CTAs are **Take the watch →** (sign in to the Halden demo) and **↺ Replay the night**. The fine print, in mono, reads: "For mid-size companies with operations in the EU, US and Australia · designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting".

**The status line is the landing's navigation.** Its cells are `22:00 takes in everything · 01:30 sorts real from noise · 03:12 starts the regulator's clock · 08:00 briefs the CISO · sign in`. Each one jumps to a **shift-log section** below the fold. The left margin of each section holds a large Geist Mono timestamp, sticky while you read:

- **22:00:** "Every sign-in, email, laptop and cloud account, all night." A one-minute strip of 120 cells, two of them amber.
- **01:30:** "212 alerts from one broken backup job. One note, not 212 pages."
- **03:12:** "When an incident is reportable, the clock starts on its own." A three-row regime ledger: NIS2 24 h · 72 h · final report within a month; SEC 8-K four business days from the materiality decision; SOCI 12 h · 72 h.
- **08:00 "first light":** the page background turns from night to **dawn** (#0F2830 → #CFE7E1), and a white brief card previews §6.11.

The footer is on the dawn colour: "Take the watch at Halden Logistics." It includes the sample-data note and "does not submit anything without a person".

`skip replay →` (bottom right of the centre) and `prefers-reduced-motion` both jump to the final state. With reduced motion the clock still ticks once a second without rolling.

---

## 5. Home: "the Watch" (`/watch`)

The home is the **live console** in the person's role layout. There is no greeting and there are no KPI cards. The responder layout is the prototype (`console.html`):

- **events** (300 px): the live river, grouped by minute. Each minute header reads like "09:18 · 1,262 in · 2 to look at".
  - Unusual-behaviour rows: ◇ with an amber left rule.
  - Incident-related rows: ▸ with a teal rule.
  - Routine rows are folded into a dashed block.
- **incident** (flexible): the incident with the most urgent clock or the oldest item waiting for you. It shows the header, ATT&CK chain, stage rail, replay controls and the **baton timeline** (§6.4).
- **awaiting you** (344 px):
  - the approval card (does / why / undo, Hold, "Decline with a reason", "Ask Response why");
  - the draft notice for the most urgent clock, with its required-fields checklist;
  - the actions already authorised in this incident.

When nothing is open, the incident tile reads "Nothing needs a person. The watch is quiet." and suggests a saved hunt. The status line reports what changed since you last looked ("Since 18:40: 2 incidents, 1 contained, 1 waiting for you").

---

## 6. Screens and wireframes

Screens marked **[P]** are prototyped. Every wireframe sits inside the clock bar + tiles + status line shell unless it says otherwise.

### 6.1 Landing (`/`) [P]: `index.html`

```
┌ ◉ nightjar soc │ NIS2·BE early warning 23:59:27 ○ │ NIS2·BE full 71:59:27 ○ │            [↺ Replay the night] [Take the watch →] ┐
├────────────────────┬──────────────────────────────────────────────────────────────────────┬───────────────────────────────┤
│ events  Halden     │  ● replay · Halden Logistics · the night of Wednesday to Thursday    │ incident  INC-0412            │
│ 03:12:06 Laptop …  │  03:14:13                         ← Geist Mono 250, rolls (Tick)     │ ┌───────────────────────────┐ │
│ 03:11:53 Backup …  │  03:12:04  Marta in accounts payable signs in from a new country.    │ │▮▮▮▯ High INC-0412 contained│ │
│ ◇ 03:12:04 Marta…  │  03:12:31  Three alerts become one incident.                         │ │Account takeover after a … │ │
│ 03:11:40 Payment…  │  03:12:58  Triage: likely real, 94 in 100. Enrichment: domain 2 d.  │ │ T  E  R  P                │ │
│ …                  │  03:13:40  Reportable under NIS2. The 24-hour clock starts …         │ │ ○╮          Likely real   │ │
│ (mask fades out    │  03:14:12  Priya, on call, held to authorise. Account disabled.     │ │  ╰○╮        Sender domain │ │
│  toward the floor) │  ─────────────────────────────────────────────────                   │ │    ╰○╮      Disable acct? │ │
│                    │  Someone is always                                                    │ │      ╰○     Priya held ✓  │ │
│                    │  on watch.                       ← Geist 600, 60 px                  │ └───────────────────────────┘ │
│                    │  sub-line (17.5 px)                                                   │ Disable Marta's account …     │
│                    │  [Take the watch →]  [↺ Replay the night]                             │ [✓ Authorised · 03:14:12    ] │
│                    │  For mid-size companies … designed to support NIS2, SEC 8-K, SOCI     │  Logged with who, when, why.  │
├────────────────────┴──────────────────────────────────────────────────────────────────────┴───────────────────────────────┤
│ ● replay │ 22:00 takes in everything │ 01:30 sorts real from noise │ 03:12 starts the regulator's clock │ 08:00 briefs … │ sign in │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  ↓ shift log: [22:00 │ every sign-in…] [01:30 │ 212 alerts…] [03:12 │ regime ledger] [08:00 first light │ white brief card on dawn]
```

### 6.2 Sign-in (`/sign-in`)

A single centred tile on the night background. The clock bar idles above it ("No regulator clocks running") and the status line below reads "● Taking in events normally · Halden Logistics demo". The demo account picker uses roles, not names.

```
┌ ◉ nightjar soc │ No regulator clocks running                                                              ┐
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                         ┌────────────── take the watch ──────────────┐                                       │
│                         │ Work email   [ priya.nair@halden.example ] │                                       │
│                         │ Password     [ ••••••••••••             ] │                                       │
│                         │ [ Take the watch → ]                       │                                       │
│                         │ ── or try the Halden Logistics demo as ── │                                       │
│                         │ ( Analyst ) ( Responder ) ( CISO ) ( Auditor ) │  ← mono chips; CISO lands on brief │
│                         │ Forgot password · Sample company and data  │                                       │
│                         └────────────────────────────────────────────┘                                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ● Taking in events normally · Halden Logistics demo                                          ⌘K after sign-in │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 6.3 Live console home (`/watch`) [P]: `console.html`

```
┌ ◉ nightjar soc │ NIS2·BE early 17:53:38 ○ Draft ready │ NIS2·BE full 65:53:38 ○ │ SOCI·AU 72 h 61:21:34 ○ │ SEC 8-K not started ◆ │ ⌘K ☼ ┐
├──────────────────────────┬───────────────────────────────────────────────────────────────┬─────────────────────────────┤
│ events  live  1,284/min  │ incident INC-0412                     opened 03:12:31  ⌘\      │ awaiting you 1   Priya Nair │
│ 09:18 1,262 in · 2 to …  │ ▮▮▮▯ High · Reportable: NIS2 (Belgium) · 3 alerts grouped       │ [‖ Awaiting your approval]  │
│ ◇ Shuo Chen downloaded … │ Account takeover after a phishing email                        │ Isolate Marta's laptop      │
│ · Dirk Verbeek signed in │ Marta Lindqvist · accounts payable, Antwerp · HL-ANT-LT-044     │ does  Cuts it off …         │
│ ▸ Response asked you to… │ ATT&CK [T1566.002] → [T1078] → [T1114.003]                      │ why   Opened the link …     │
│ ┆+1,257 routine folded┆  │ ✓Triage ── ✓Investigate ── ‖Contain 1 waiting ── ○Report 2 clk │ undo  Release any time      │
│ 09:17 1,301 in · 1 to …  │ [⏮][▶ Replay][⏭] [1×][4×] ▬▬▬▬▬▬▬▬▬  step 9 of 9 · 09:02:40   │ [◔ Hold to authorise   64%] │
│ ◇ Kenji Ito failed …     │ (baton timeline, §6.4)                                          │ Decline · Ask Response why  │
│ …                        │                                                                 │ notice · draft 2  due Fri…  │
│                          │                                                                 │ NIS2 early warning → CCB    │
│                          │                                                                 │ ✓ ✓ ✓ ◐ Contact person      │
│                          │                                                                 │ [Open the draft][Compare]   │
├──────────────────────────┴───────────────────────────────────────────────────────────────┴─────────────────────────────┤
│ ● Taking in events normally │ Triage · Enrichment · Response on shift │ Priya Nair · responder │ layout ⌘[ ] │ ⌘K go anywhere │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 6.4 Incident detail with the replayable investigation timeline (`/incidents/[id]`) [P]

The incident tile, zoomed (`⌘⇧↵`) or deep-linked. The **baton timeline** is the signature. It has four narrow **lane tracks** (T triage · E enrichment · R response · P people, 62 px each), one dot per step on its lane, and S-curve hand-off lines between consecutive steps. To the right sits a wide "what happened, and why" column. Each row shows:

- a title in Geist 13.5/500;
- a mono line: `Lane · looked at … / why …`.

The current step is lit; a step waiting for a person is a lavender band with `‖` and a dashed line into the people lane. **Replay** puts a playhead row on step 1 and plays at 1 step per 1.2 s (or 4×). The baton bead travels along each hand-off, rows after the playhead dim to 35%, and `←/→` step. "All activity" (toggle) expands every tool lookup between key steps, in the style of incident.io's "View all activity".

```
│ [⏮][▶ Replay][⏭] [1×][4×] ▬▬▬▬▬▬▬▬▬ step 9 of 9 · 09:02:40          [all activity ○] [export replay]│
│          (T)   (E)   (R)   (P)    what happened, and why                                               │
│ 03:12:31  ○     ┆     ┆     ┆     Grouped 3 alerts into one incident                                   │
│           │                       Triage · new-country sign-in · inbox rule · reset                    │
│ 03:12:58  ○     ┆     ┆     ┆     Likely real: 94 in 100                                               │
│           ╰──╮                    Triage · why new device + rule 4 min later                           │
│ 03:13:10     ○  ┆     ┆           Sender domain is 2 days old                                          │
│ 03:13:24     ○  ┆     ┆           Laptop has 1 exploited weakness · looked at asset list, CISA KEV     │
│              ╰──╮                                                                                      │
│ 03:13:40        ○     ┆           Plan: disable, sign out, isolate · 3 actions · each needs a person   │
│                 ╰──╮                                                                                   │
│ 03:14:12           ○              ✓ Priya held to authorise · account disabled · signed out            │
│        ╭───────────╯                                                                                   │
│ 03:14:30  ○                       Reportable under NIS2 · 2 clocks started · notices drafted           │
│ 03:15:02     ○                    No payments left Halden · looked at the payments log                 │
│ ▌09:02:40       ○┄┄┄┄◉            Asks to isolate the laptop · ‖ handed to people · awaiting           │ ← lavender band
```

Selecting a row opens a **step drawer** inside the tile. It shows the evidence the step cited (event lines and enrichment facts, each with its source), the reasons with their weights in plain words ("New device: strong · Inbox rule soon after sign-in: strong · Same sender as 2 confirmed cases: medium"), and **"Disagree"**. Disagreeing records a person's verdict, which also feeds the triage evaluation set. Text that came from log fields is shown as **quoted evidence** in mono, never as instructions, under the label "from the event, as received".

### 6.5 Approval flow: hold to authorise [P]

Every response action (isolate a laptop, disable an account, sign out everywhere, block a sender, revoke a key) arrives as an **approval card**:

```
┌ [‖ Awaiting your approval] ───────────────────────────── 1 of 2 ┐
│ Isolate Marta's laptop                                            │
│ HL-ANT-LT-044 · proposed by Response at 09:02 · INC-0412          │
│ does  Cuts the laptop off from the network, except Nightjar SOC.  │
│ why   It opened the phishing link at 03:09 and has one weakness   │
│       that is being exploited in the wild.               [why ↗] │
│ undo  Release from isolation at any time. Logged either way.      │
│ who   Marta Lindqvist will see: "Your laptop is offline for a     │
│       security check. IT will contact you." (editable)            │
│ [ ◔  Hold to authorise · or hold Space for 1.2 s          64% ] │  ← fill sweeps left→right, ring arc fills
│ Decline with a reason                          Ask Response why   │
└───────────────────────────────────────────────────────────────────┘
   after: [ ✓  Authorised · 09:21:07 · by Priya Nair             ]  ← stamp, teal fill
          baton row "✓ Priya held to authorise" appends to the replay
```

- **Release early cancels** (the ring springs back to 0 in 240 ms). The button is a real `<button>` with `aria-describedby` "Press and hold for 1.2 seconds, or hold Space or Enter."
- **Decline** needs a reason chosen from a short list (wrong host, business-critical now, false positive, other with text). The reason goes back to Response, which may propose something else.
- **Least privilege:** a person can only authorise actions their role permits. The responder can isolate and disable; the analyst can block a sender; the auditor sees cards greyed out with "Read only".
- **Two-person rule** (setting): actions on executives' accounts or servers need two holds from different people. The card shows "1 of 2 held · waiting for a second person".
- **Accessibility alternative** (personal setting): "Confirm by typing the device name" replaces the hold for people who cannot hold.

### 6.6 Incident clock, notice drafting and submission (mock) (`/incidents/[id]/notice/[regime]`)

You reach it by clicking a clock cell or "Open the draft". The tile splits into **draft | evidence**:

```
┌ notice · NIS2 early warning · Belgium ─────────────── due Fri 21 Nov 03:12 · 17 h 53 m ○ On track ┐
│ To   CCB, Centre for Cybersecurity Belgium (from Settings › Regime contacts)    draft 2 · saved 09:14 │
├──────────────────────────────────────────────────┬──────────────────────────────────────────────────┤
│ REQUIRED FOR AN EARLY WARNING                     │ evidence                                          │
│ ✓ When Halden became aware   today 03:12 [E-03]  │ E-03 03:12:04 sign-in, new country (as received) │
│ ✓ Suspected unlawful act     yes          [E-07] │ E-07 03:13:10 sender domain registered 2 days ago │
│ ✓ Cross-border impact        possibly NL, DE [E-11]│ E-11 customers on shared invoicing in NL, DE     │
│ ◐ Contact person             [ Henrik Dahl ▾ ]  │                                                  │
│ ── narrative (editable, plain language) ──────── │ (click an [E-xx] to scroll and highlight here)   │
│ On 20 November 2025 at 03:12 CET Halden Logistics │                                                  │
│ became aware of unauthorised access to one        │                                                  │
│ employee mailbox following a phishing email …     │                                                  │
│ ── what comes next ──                              │                                                  │
│ ○ Incident notification   due Sun 23 Nov 03:12   │                                                  │
│ ○ Final report            within one month of it  │                                                  │
├──────────────────────────────────────────────────┴──────────────────────────────────────────────────┤
│ [Review and submit]  → preview lists any ◐ still missing  →  [Hold to submit (mock)]                 │
│ after: ✓ Submitted 09:31 by Henrik Dahl · reference CCB-mock-2025-1120-01 · clock stops, cell turns  │
│        "✓ Sent" for 10 min, then leaves the bar; the next stage's clock stays                       │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Regime determination** runs from the company profile (sectors, countries, listing) and the incident facts. Each clock cell explains why it applies ("Transport sector, Belgium, significant incident test: met").
- **Timing rules** are computed at runtime:
  - **NIS2:** early warning 24 h, incident notification 72 h, final report one month after the notification, intermediate reports on request.
  - **SEC 8-K Item 1.05:** four business days *from the materiality determination*, so it shows "not started" until a person records it. The business-day count uses the US federal holiday calendar.
  - **SOCI Part 2B:** 12 h when significant impact is suspected, 72 h for relevant impact, with a written report after any verbal one.
- **Submission is mocked:** it records who, when, what was sent and a mock reference. The UI says **"Recorded as submitted (demo)"**. Nothing is ever sent without a person holding to submit.

### 6.7 Event stream and hunting (`/hunt`)

The hunt tile opens beside the incident (analyst layout). The search bar takes plain words or field filters. Field names display as friendly labels, and the underlying query is never called a query language in the UI.

```
│ hunt                                                         [List | Timeline]  [Save as…] [▶ Run] │
│ [ laptops that opened links from halden-logistlcs.com in the last 7 days            ] ⏎            │
│ filters: (domain = halden-logistlcs.com ×) (event = link opened ×) (last 7 days ▾) + add           │
│ suggestions: ▸ Who else got mail from this sender?  ▸ Sign-ins from new countries this week         │
│ 4 matches · 3 laptops · 2 people                                         searched 18.2 m events     │
│ time        who               device          what                                    incident     │
│ 03:09:12    Marta Lindqvist   HL-ANT-LT-044   opened link in "Invoice 4471 overdue"   INC-0412     │
│ 03:09:40    Marta Lindqvist   HL-ANT-LT-044   entered password on look-alike page     INC-0412     │
│ Tue 14:02   Jonas Peeters     HL-ANT-LT-019   opened link, page blocked by browser    — [open +]   │
│ Mon 10:31   Sara Vos          HL-RTM-LT-007   opened link, no password entered        — [open +]   │
```

**Timeline** mode draws the same rows as a vertical **minute ladder** (time down the left, one row per match). It is never a horizontal scrubber. "Open +" adds a match to an incident.

### 6.8 Saved searches (`/hunt/saved`)

```
│ saved searches                                                   [New search] (⌘K › "saved")       │
│ name                                       owner        runs          last result     alert me     │
│ ▸ Laptops that opened the same link        Priya Nair   on demand     4 · today 09:20   —          │
│ ▸ Sign-ins from new countries (finance)    Ana Ruiz     every hour    1 · 03:12 ◇       ● on       │
│ ▸ Mail forwarding rules to outside domains  Ana Ruiz     every 15 min  0 · 09:15         ● on       │
│ ▸ Keys or tokens pushed to public code     shared       every hour    0 · 09:00         ● on       │
```

Each saved search keeps a short note ("why we watch this"). "Alert me" turns it into a detection with an owner, which then appears in Rules.

### 6.9 Unusual behaviour (`/behaviour`)

Each person or device is a **rhythm strip**: 24 h × 14 days of small cells, toned by how far from their own normal. ◇ marks the outliers, and the reasons appear in plain words. There is no world map, and no score without reasons.

```
│ unusual behaviour  ·  last 14 days  ·  [people | devices]               sorted by: most unusual ▾   │
│ Shuo Chen · finance, Rotterdam        ◇ 9× her usual download volume, 09:18 today                   │
│   00 ░░░░░░▒▒▓▓▓▓▒▒░░░░  … 14 rows of 24 cells …  ◇ ← today 09:00                                   │
│   reasons: volume far above her 14-day normal · new share · within working hours (lowers concern)   │
│   [Open as incident] [Ask her manager (draft message)] [Mark as expected: month-end]               │
│ Kenji Ito · ops, Melbourne            ◇ 3 failed sign-ins then success from a new device, 09:17     │
│ HL-ANT-SRV-03 · file server           ◇ outbound connections at 02:00, first time in 14 days       │
```

"Mark as expected" asks how long, and the change is logged.

### 6.10 Detection rules (`/rules`, `/rules/[id]`)

```
│ rules · 214 on · 6 noisy this week                                    [Import Sigma] [New rule]   │
│ rule                                         ATT&CK         last 7 days        likely real   state │
│ New inbox rule forwards outside Halden       T1114.003      12 · ▁▂▁▁▃▁▂       71 in 100     ● on  │
│ Sign-in from a never-seen country            T1078          31 · ▂▃▂▂▅▂▃       38 in 100     ● on  │
│ Backup job failure storm                     —              212 · ▁▁▁▁▁█▁     2 in 100 ◐   ● noisy│
│ ── /rules/[id] ──────────────────────────────────────────────────────────────────────────────────── │
│ When   a mailbox rule is created  and  it forwards to a domain outside Halden                       │
│ Group  alerts for the same person within 30 minutes into one incident                               │
│ [Readable | Source (Sigma)]    Test on last 7 days ▶  → 12 matches · 9 confirmed · 3 noise         │
│ Owner Ana Ruiz · Changed 3 Nov by Ana Ruiz · version 4 · [Lower the noise…] [Turn off (with reason)]│
```

The noisy state uses ◐ + the word "noisy", never colour alone. "Likely real" shows the triage score's calibrated average for that rule.

### 6.11 Executive morning brief, light (`/brief`) [P]: `brief.html`

A different reading experience: **one column, editorial, dawn-washed, finishable in two minutes.** It has no tiles, no river, no KPI cards and no greeting. The clocks appear as quiet ledger rows, not a bar. A pale mint "first light" wash sits at the top, with faint sun rings behind the right margin. On the right is a 300 px margin column.

```
┌ ◉ nightjar soc │ morning brief · Halden Logistics                 Weekly report  Open the watch  [⌘K] ┐
│                                                                                                         │
│  Thursday, 20 November 2025 · prepared 07:58 for Henrik Dahl, CISO · a 2-minute read │ posture        │
│                                                                                       │ Steady — same  │
│  One incident from overnight is                ← Geist 500, 46 px                     │ ╱╲__╱‾‾‾‾‾‾•   │
│  reportable. It is contained, and                                                      │ Patching in …  │
│  one decision is yours today.                  ← teal phrase                          │───────────────│
│                                                                                       │ approved       │
│  At 03:12 someone took over Marta Lindqvist's mailbox … No payments left Halden. …   │ overnight · by │
│                                                                                       │ a person       │
│  ▌◆ Needs your decision · SEC Form 8-K, Item 1.05                                      │ ✓ Revoke key   │
│  ▌Is the Antwerp account takeover material to Halden Logistics?                        │ ✓ Disable acct │
│  ▌Halden's shares are listed in the US … decision belongs to you and the committee.    │ ✓ Sign out     │
│  ▌┌ the watch's view · 3 reasons ─────────────────────────────┐                        │ ‖ Isolate …    │
│  ▌│ Probably not material: one mailbox, no money moved …       │                        │───────────────│
│  ▌└─────────────────────────────────────────────────────────────┘                        │ on watch now  │
│  ▌[Record the decision] [Ask the watch lead]  If material: 8-K due Wed 26 Nov          │ Priya Nair …  │
│                                                                                       │               │
│  Clocks running  3 · all on track                                                     │               │
│  NIS2 early warning · Belgium        due tomorrow 03:12        17 h 53 m  ○ On track  │               │
│  ▬▬▬▬▬▬▬ (elapsed rule)                                                               │               │
│  NIS2 incident notification · BE     due Sun 23 Nov 03:12      65 h 53 m  ○ On track  │               │
│  SOCI 72-hour report · Australia     due Sat 22 Nov 22:40      61 h 21 m  ○ On track  │               │
│                                                                                       │               │
│  Overnight, in plain words                                                            │               │
│  22:40  A cloud access key for the Melbourne booking system appeared in public code … │               │
│  01:30  A failed backup job set off 212 alerts … No one was woken.                    │               │
│  03:12  The Antwerp account takeover above …                                          │               │
│  06:10  Shuo Chen … looks like month-end work; nothing is blocked.                    │               │
│  — the night watch: Triage, Enrichment and Response, with Priya Nair on call         │               │
│                                                                                       │               │
│  Seven nights   18:00 to 08:00 · ◆ incident · ● worth a look                          │               │
│  Thu night ·──·──·────◆──────·────── …   (7 rows, last night labelled)                │               │
│  This week the watch took in 1.9 million events … Every one of the 7 response actions │               │
│  was authorised by a person.                                                          │               │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **The decision** opens a sheet with three outcomes: *Material* (starts the SEC clock with a Drop into the analyst's bar and a draft Item 1.05), *Not material, with reasons* and *Not yet: decide by…*. Each outcome is recorded with a timestamp.
- The **dark "night edition"** of the brief uses the same layout on #0A1016 with a dusk wash, for executives who read late.
- **Mobile:** a single column, with the margin column moved under the decision.

### 6.12 Weekly report (`/brief/week/[isoWeek]`)

It continues the brief's language: a document, not a dashboard. It is printable and exportable as PDF.

```
│ Week 47 · 14–20 November 2025 · Halden Logistics · prepared for the board                         │
│ The week in one paragraph (Geist 500, 28 px)                                                       │
│ Seven nights (as in the brief, 7 rows) ─────────────────────────────────────────────────────────── │
│ Incidents, as told by their replays                                                                 │
│  ◆ INC-0412 Account takeover, Antwerp · reportable NIS2 · contained in 2 min · 3 actions approved  │
│     "What happened / what we did / what we changed" (from the curated replay)                      │
│  ◆ INC-0409 Cloud key leak, Melbourne · reportable SOCI · …                                        │
│ Clocks this week: 4 notices, all sent on time (✓ 4 of 4) · median 6 h 10 m before the deadline     │
│ Noise: 3 rules turned down · triage closed 1,212 alerts as noise · people overturned 2 verdicts    │
│ Time to triage · time to contain after approval (median, p90) as one sentence + a small dot plot   │
│ Next week: SOCI written report due · 2 laptops in Antwerp behind on patches                        │
```

### 6.13 Assets and identities (`/assets`, `/identities`)

A ledger table with a detail tile, not a card grid. Each row leads with the business role ("Payments laptop", "Booking system").

```
│ assets · 41 · [laptops | servers | cloud | all]   filter: Antwerp ▾ · behind on patches ◐          │
│ name              what it does            where       owner            patch       known weakness   │
│ HL-ANT-LT-044     Payments laptop         Antwerp     Marta Lindqvist  ◐ 12 d      ▲ 1 exploited    │
│ HL-MEL-APP-02     Booking system (cloud)  Melbourne   Ops AU           ✓ current   —               │
│ ── selected: HL-ANT-LT-044 ── incidents (1) · events today (212) · isolation: ‖ requested           │
│ identities · 186 people · 23 service accounts                                                       │
│ person            team / site             sign-in normal          unusual (14 d)   access           │
│ Marta Lindqvist   AP · Antwerp            BE · 07–18 h · 1 device ◇ 1 today        disabled 03:14   │
```

### 6.14 Settings (`/settings/regimes`, `/settings/members`)

A two-column form page inside one zoomed tile. Sections are reached through ⌘K or the section list at the top of the tile (a list, not a sidebar).

```
│ settings › regime contacts                                                                          │
│ Which rules apply to Halden Logistics?   (from your company profile; change in Company)             │
│ ● NIS2 · Belgium        transport sector · essential entity      authority: CCB                     │
│     primary contact [Henrik Dahl ▾]  backup [Priya Nair ▾]  submit as [Henrik Dahl ▾]  ✓ verified   │
│ ● SEC Form 8-K · US     listed company · disclosure committee [3 people ▾]                          │
│ ● SOCI · Australia      critical freight services asset · authority: ASD's ACSC                     │
│     primary [Kenji Ito ▾]  backup [Ops AU ▾]   12 h phone line [+61 …]                              │
│ ○ Add a regime                                                                                      │
│ settings › members                                                [Invite someone]                  │
│ name            role                 can authorise                         last seen                │
│ Priya Nair      Responder            isolate · disable · sign out          now                      │
│ Ana Ruiz        Analyst              block sender                          08:40                    │
│ Henrik Dahl     CISO / executive     submit notices · materiality decision 07:58                    │
│ Lotte Maes      Auditor (read only)  —                                     Mon                      │
│ two-person rule for executives' accounts and servers   [● on]                                       │
```

### 6.15 Activity log (`/activity`)

Every sensitive action, newest first. Each row ends with a short seal so the auditor can check the chain. The auditor's layout pairs it with the incident replay.

```
│ activity · 18,402 entries · [Verify the log]  → "Chain intact · checked 18,402 entries · 09:31"    │
│ time       who                 did                                          about         seal      │
│ 09:31:07   Henrik Dahl         submitted NIS2 early warning (demo)          INC-0412      ·a91f     │
│ 09:21:07   Priya Nair          held to authorise: isolate HL-ANT-LT-044     INC-0412      ·77c2     │
│ 09:02:40   Response (teammate) proposed: isolate HL-ANT-LT-044              INC-0412      ·0be4     │
│ 03:14:12   Priya Nair          held to authorise: disable m.lindqvist       INC-0412      ·5d10     │
│ filters: (people | teammates | system) (approvals) (notices) (settings)        [Export evidence pack]│
```

Teammates appear as "Response (teammate)", never by internal names. A teammate's row can open the replay step it came from.

### 6.16 Mobile (390 px)

```
┌ ◉  NIS2 17:53 · +2 ▾ ┐   one tile per screen
│ incident INC-0412    │   swipe ← → between tiles
│ ▮▮▮▯ Account takeover│   baton tracks shrink to 4 × 18 px
│ T E R P  what…       │
│ ○        Grouped …   │
│ ...                  │
│ [◔ Hold to authorise]│   full width, vibrates once when done
│ ● ● ○                │   tile dots
└──────────── (⌕) ─────┘   floating ⌘K button
```

---

## 7. Type

**Geist** for UI and reading, **Geist Mono** for time, IDs, labels and keycaps. There is **no serif** anywhere (Plotline and Kinroot own serif). Labels are **lowercase mono** ("events", "awaiting you", "notice · draft 2"), never uppercase-tracked mono (Plotline's).

| Token | Face / weight | Size / line | Use |
|---|---|---|---|
| `display-clock` | Geist Mono 250 | 112 / 1 (landing), 64 settled | the replay clock, rolling digits |
| `display` | Geist 600, −0.035em | 60 / 1.02 | landing headline |
| `brief-h1` | Geist 500, −0.03em | 46 / 1.12 | brief headline (`text-wrap: balance`) |
| `h1` | Geist 600, −0.015em | 23 / 1.2 | incident title |
| `h2` | Geist 600 | 18–19 / 1.25 | approval title, decision question (23 in brief) |
| `clock` | Geist Mono 500, `tnum` | 19 / 1 | clock bar digits |
| `brief-clock` | Geist Mono 300 | 34 / 1 | time left in the brief ("17 h 53 m") |
| `body` | Geist 400 | 13–14 / 1.45 (console), 16–19 / 1.6 (brief) | text |
| `meta` | Geist Mono 400 | 11–12.5 / 1.3 | timestamps, "looked at", pane titles, keycaps |

Numbers in mono always use `tnum`. Geist Mono's slashed zero is kept: it is part of the console's identity and avoids O/0 confusion in IDs.

---

## 8. Palette (tokens)

Mint teal (hue ≈170°) is the only accent. It is clearly not Kinroot's oxblood (350°) or Plotline's surveyor orange (25°), and it is used for **light and line** (live dots, the baton, focus, "on track", the active-tile rule), almost never as a large fill. The two exceptions are the primary landing CTA and the "Authorised" stamp.

### Console: night (default)

| Token | Hex | Use |
|---|---|---|
| `bg` | `#070B10` | page, gutters |
| `pane` | `#0C1219` | tiles |
| `raised` | `#121A23` | hover, chips, ATT&CK tags |
| `sunk` | `#090E14` | keycap fill |
| `line` | `#1A2430` | hairlines |
| `line-strong` | `#283646` | control borders |
| `ink` / `ink-2` | `#E3EBF1` / `#B9C6D0` | text (15.6 / 10.8 : 1 on pane) |
| `muted` | `#8191A0` | secondary text (5.8 : 1) |
| `faint` | `#75879A` | meta and timestamps (5.1 : 1 on pane, 4.75 on raised) |
| `teal` | `#3CE6C8` | accent, live, teammates, on track (12.5 : 1) |
| `teal-ink` | `#062A24` | text on teal fills (9.8 : 1) |
| `teal-wash` / `teal-line` | `rgba(60,230,200,.10)` / `.38` | selection, hold track |
| `amber` | `#F2B84B` | unusual behaviour ◇, due soon ◐ |
| `red` | `#FF6B5E` | severity bars, overdue ● |
| `lav` | `#B6A8FF` | needs a person ‖ / ◆ |

### Console: day watch (light)

| Token | Hex |
|---|---|
| `bg` / `pane` / `raised` | `#E9EEF0` / `#FFFFFF` / `#F3F6F7` |
| `line` / `line-strong` | `#DCE3E7` / `#C4CED4` |
| `ink` / `ink-2` / `muted` / `faint` | `#0B141B` / `#2B3943` / `#53626E` / `#5F6E78` (faint is 4.85 : 1 on white) |
| `teal` | `#0A7F6D` (4.9 : 1 on white) |
| `amber` / `red` / `lav` | `#8A5D00` / `#B42318` / `#5B45C9` |

### Brief: dawn (light, default) and night edition (dark)

| Token | Dawn | Night edition |
|---|---|---|
| `page` | `#F7F9F8` | `#0A1016` |
| `wash-a → wash-b` (top 760 px, masked out by 55%) | `#CBE5DE → #E4F0EC` | `#142A30 → #0E1A20` |
| `ink` / `ink-2` | `#0D1A20` / `#33444C` | `#E6EDF1` / `#BAC7CE` |
| `muted` / `faint` | `#4C5B63` (5.3 : 1 on the wash) / `#5E6D74` | `#8F9EA8` / `#8392A0` |
| `rule` / `rule-2` | `#DCE4E2` / `#C9D4D1` | `#1B2731` / `#283643` |
| `teal` / `lav` | `#0A7F6D` / `#5B45C9` | `#3CE6C8` / `#B6A8FF` |
| sun rings | `rgba(10,127,109,.16)` | `rgba(60,230,200,.14)` |

Landing dawn transition (08:00 section): `#070B10 → #0F2830 (30%) → #CFE7E1 (75%) → #EEF5F2`.

---

## 9. Status language (never colour alone)

Every state has a glyph and a word. Colour only reinforces them.

| State | Glyph + words | Colour |
|---|---|---|
| Clock on track | `○ On track` | teal |
| Clock due soon (under 25% of the window left) | `◐ Due soon` | amber |
| Clock overdue | `● Overdue` | red |
| Clock not started, needs a decision | `◆ Materiality decision` + dashed underline | lavender |
| Clock done | `✓ Sent` | teal |
| Awaiting a person | `‖ Awaiting your approval` | lavender |
| Authorised | `✓ Authorised · time · by name` | teal fill (stamp) |
| Declined | `✕ Declined · reason` | muted |
| Severity | bar glyph `▮▮▮▯ High` · `▮▮▯▯ Medium` · `▮▯▯▯ Low` · `▮▮▮▮ Critical` | red / amber / muted |
| Unusual behaviour | `◇` + "unusual behaviour" | amber rule |
| Incident-related event | `▸` | teal rule |
| Required field missing | `◐` + "confirm …" | amber |
| Rule noisy | `◐ noisy` | amber |
| Teammate lane | `T` `E` `R` in teal rings · `P` (people) in a lavender ring | — |
| Stage rail | `✓ Triage — ✓ Investigate — ‖ Contain — ○ Report` | teal / lavender / muted |

---

## 10. Component shape language

- **Tiles** have radius 0, 1 px hairline gutters and no shadows. The active tile has a 2 px teal top rule.
- The **clock bar and status line** have radius 0 and are divided by 1 px vertical rules.
- **Controls** have a 6 px radius. Chips and keycaps have a 4 px radius; keycaps have a 2 px bottom border.
- The **hold button** has an 8 px radius and a 56 px height. It is a teal wash with a teal line, plus a ring with the progress arc.
- **Popovers** (⌘K, step drawer) have a 10 px radius and the only shadow in the system.
- **Density:** console rows are 28–50 px. The river and pane titles are mono. Emphasis comes from a 2 px left rule (◇ amber, ▸ teal, ‖ lavender), never from filled cards.
- **Data idioms:** streams (the river), strips (the incident strip, the stage rail), lane tracks (the baton), ledgers (assets, rules, activity, the brief's clocks) and rhythm strips (unusual behaviour). There are no KPI cards, no donut charts and no world maps.
- **Brief:**
  - paper-quiet with ledger rules;
  - the only box is "Your decision", with a 3 px lavender left rule and a 4 px radius;
  - the CTA is ink-filled, not teal.
- **Logo:** a nightjar's eye (an upper arc, a fainter lower arc and a dot) in teal, with "nightjar" in Geist Mono 500 lowercase and "soc" in muted.

---

## 11. Motion: "Live ticks"

Svelte built-ins only. Everything animates transform or opacity, except the hold fill (scaleX) and the SVG ring (`stroke-dashoffset` on a **progress ring**, which is a gauge, not a self-drawing outline).

| Name | What moves | Duration / easing (Svelte) | Reduced motion |
|---|---|---|---|
| **Tick** | each digit is a vertical 0–9 strip and rolls to its value (`translateY(-n em)`); only changed digits move | 420 ms `cubic-bezier(.2,.9,.1,1)` at 1×; 140 ms during accelerated replay. Implemented as a `Digit.svelte` with `Tween` (`easing: cubicOut`) | digits swap with no roll |
| **Coalesce** | three alert rows fly from the river into the incident strip; the strip scales .94 → 1 | `crossfade` (`send`/`receive` keyed by alert id), 560 ms `cubicInOut`, 70 ms stagger; strip `Spring({stiffness: .15, damping: .55})` | rows fade out (120 ms), strip appears |
| **Baton** | an 18 × 6 px teal bead travels along the hand-off curve (`offset-path`); the receiving dot pulses 1 → 1.35 → 1; the line fades in behind the bead | bead 900 ms `cubic-bezier(.65,0,.35,1)`; pulse 280 ms; line opacity 360 ms after a 380 ms delay | no bead; lines and dots present |
| **Hold** | while held: fill sweeps `scaleX(0 → 1)` and the ring arc fills, linearly, with a % readout; release: springs back; complete: a stamp | 1200 ms linear; release 240 ms `Spring`; stamp 420 ms `cubic-bezier(.2,1.4,.3,1)` scale 1.12 → 1 | no sweep, numeric % only; **the 1.2 s hold still applies** (it is the safety, not decoration) |
| **Drop** | a new clock cell drops into the top bar from −56 px with a small overshoot; neighbours make room | 620 ms `cubic-bezier(.2,1.3,.35,1)`; neighbours `animate:flip` 300 ms; second cell delayed 120 ms | appears in place; status line announces "NIS2 clock started" |
| **River** | new rows enter from 6 px above with a fade; at most 4 rows/s are drawn, the rest fold into "+n" | 320 ms `cubic-bezier(.2,.9,.1,1)` | no movement; a "12 new · show" counter |
| **Log settle** (landing) | the newest log line arrives at 24 px, then settles to 16 px when the next arrives; at the end the clock shrinks 112 → 64 px and mutes | line enter 420 ms; settle 400–500 ms `cubic-bezier(.2,.9,.1,1)` (scale transform in the Svelte build) | final state rendered at once |
| **Replay step** | the playhead row highlight moves to the next row; later rows dim to 35% | 240 ms ease-out per step; play = 1 step / 1.2 s (1×) or 0.3 s (4×) | instant step changes |
| **Palette** | ⌘K opens with a fade and scale .985 → 1; results reorder with `animate:flip` | 160 ms / 180 ms | fade only |
| **Dawn** (watch → brief) | page transition: a horizon wipe, with the brief revealed by `clip-path: inset(100% 0 0 0 → 0)` from the bottom, as dawn rises | 700 ms `cubic-bezier(.3,.7,0,1)` via SvelteKit `onNavigate` + View Transitions | 150 ms crossfade |
| **Live pulse** | the status line's live dot pulses a 7 px ring | 2.4 s ease-out loop | static dot |

What Nightjar SOC **never** uses:
- self-drawing outlines with `stroke-dashoffset` (Plotline);
- time-ruler scrubbing (Plotline);
- slack-to-taut strings, unfold springs or stitches (Kinroot);
- 150 ms page fades as the only motion (Necessa);
- parallax, scroll-jacking, glitch effects or typing-cursor gimmicks.

`prefersReducedMotion.current` (from `svelte/motion`) switches every row of the table to its right-hand column. `?still` in the prototype shows exactly that.

---

## 12. Signature interactions

1. **Replay the night / Replay an incident.** Any incident can be replayed step by step through the baton tracks. The landing is the same replay, staged.
2. **Hold to authorise.** It is the visible proof that nothing is switched off without a person: pointer, Space or Enter, 1.2 s, and an early release cancels.
3. **The clock drops in.** When an incident is judged reportable, its clocks drop into the top bar and start counting. Click one to open the notice draft beside its evidence.

---

## 13. Copy voice

Terse, operational, time-stamped, calm. Write short declaratives with a time in front of them. Use sentence case: no exclamation marks, no emoji and no "Oops". Name the person and the thing ("Isolate Marta's laptop"), then why, then how to undo it.

- **CTAs and verbs:**
  - landing and sign-in: Take the watch · Replay the night;
  - approvals: Hold to authorise · Decline with a reason · Ask Response why · Release from isolation;
  - notices: Open the draft · Compare with evidence · Review and submit · Hold to submit (demo);
  - decisions: Record the decision · Ask the watch lead · Mark as expected;
  - rules and hunting: Lower the noise · Save as… · Open as incident;
  - activity: Verify the log · Export evidence pack.
- **Microcopy examples:**
  - "Contained."
  - "Awaiting your approval."
  - "Nothing needs a person. The watch is quiet."
  - "Taking in events normally."
  - "Catching up: 4 minutes behind."
  - "No one was woken."
- **Teammates** are "Triage", "Enrichment" and "Response". Collectively they are "the watch" or "three AI teammates". What they do is "looked at…", "why…" and "handed to people". Scores read "94 in 100" or "likely real" / "likely noise".
- **Compliance claims:** "designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting", "aligned with". Never "compliant", "certified", "guarantees", "approved by" or "files for you". Submissions in the demo read "Recorded as submitted (demo)".
- **As-of date (late November 2025):** the docs and copy in the repo are written as of late November 2025, and nothing hard-coded refers to later events.
  - Every time-sensitive value is computed at runtime from one clock helper (`NIGHTJAR_DEMO_TODAY`, with Playwright `clock.setFixedTime` for shots): countdowns, due dates, "today/tomorrow", the "night of Wednesday to Thursday" eyebrow, the SEC business-day count, the brief's dateline and the seven-nights labels.
  - Seeded incidents are placed relative to today (the reportable one became known at 03:12 local time today).
  - Prototype shots are frozen at **Thu 20 Nov 2025 09:18 Europe/Brussels**.

## 14. Forbidden words (rendered UI text)

From STANDARDS §4: RAG, retrieval, embeddings, vector, rerank, **LLM**, model or provider names (GPT, Gemini, Groq, Llama, Claude…), LiteLLM, prompt, tokens, SQL, architecture, **pipeline**, **agent internals**, **A2A**, **MCP**, latency, fallback, cache.

For this product, also avoid "agent" and "agentic" (say teammate, Triage, Enrichment, Response, "the watch"), "AI-powered", "autonomous", "smart", "magic", "algorithm", "playbook run", "SOAR", "SIEM" in executive copy, "query language", and host or IP jargon in the brief. Analyst screens may show ATT&CK IDs, Sigma, host names and event fields, because those are the analyst's own vocabulary. The prototype passes a scan for all of the above (§15).

## 15. How this differs from Necessa, Plotline and Kinroot

| Dimension | Necessa | Plotline (Field Atlas) | Kinroot | **Nightjar SOC** |
|---|---|---|---|---|
| Metaphor | Clinical worklist | Field atlas + surveyor's ledger | Investigation board + case dossiers | **The night watch: a log, clocks and a baton passed between teammates** |
| App shell | Light left sidebar with org card, grouped nav, top search | Two-tier masthead with mono caps tabs over a full-bleed map | Ink icon spine + graph board + dossier drawer | **⌘K-first tiled console: regulator clock bar on top, hairline tmux tiles, mono status line; no sidebar, tabs or menus** |
| Landing | Headline left, product card right, 4-up stats | Huge centred serif date on topo map, scroll story | Type into the client pin, roots grow, red string | **The console replays a night (time-driven); a left-aligned night log; the headline lands as the last entry; the status line navigates to the shift log** |
| Home | "Good evening, Maya" + Today + 4 KPI cards | Map + floating ledger + time ruler | Client constellation + file tray | **The Watch: event river · incident with baton tracks · "awaiting you", in a role layout** |
| Executive view | Same app | Same app | Partners' brief inside the app | **A separate reading mode: a dawn-washed, one-column morning brief with one decision box** |
| Data idiom | Cards with score rings | Map layers, ledger rows | Graph + ledger twin | **Streams, lane tracks, clocks, ledgers, rhythm strips** |
| Type | System sans | Newsreader serif + Plex Mono caps labels | Spectral + Hanken Grotesk + Red Hat Mono | **Geist + Geist Mono; lowercase mono labels; no serif** |
| Palette / theme | Neutral grey + indigo, light | Warm map paper + orange, light | Cool bond grey + oxblood, light | **Blue-black night #070B10 + mint teal #3CE6C8, dark-first; dawn #CBE5DE → #F7F9F8 for the brief** |
| Shape | 8–12 px cards | Square boxes, rubber stamp | 2 px paper, notched folder tabs | **Radius-0 tiles with hairline gutters, keycaps, 2 px left rules, one 8 px hold button** |
| Motion | 150 ms fades | Self-drawing outlines, fly-to, ruler scrub | Unfold springs, slack-to-taut string | **Rolling digits, coalesce, baton bead, hold fill + stamp, clock drop, dawn wipe** |
| Signature | Live check progress | Ruler scrub, statement stamp | Pull the thread, fuse or split | **Replay · Hold to authorise · The clock drops in** |
| CTAs | Explore the demo / Create an account | Open the Northbank atlas / Start your atlas | Trace a client / Open Harbourline's files | **Take the watch / Replay the night** |
| Logo | Indigo "M" tile | Folded map + serif wordmark | Knotted thread + Spectral italic | **Nightjar eye (two arcs + dot) + lowercase mono wordmark** |

The screenshots were compared side by side with `Necessa 01/10`, `Plotline 01/06` and `Kinroot 01/04/06`. All three earlier products are light-first, with paper or grey canvases and serif or system type. Nightjar SOC is the only dark-first one, the only time-first one, and the only one whose landing is a live console.

One composition risk was found and fixed during iteration. A centred giant clock over a centred tagline read as a cousin of Plotline's centred giant date, so the landing's centre became a **left-aligned night log**. The mini baton graph first used `stroke-dashoffset` line drawing (Plotline's motion); the bead now carries the motion and the lines fade in.

## 16. Prototype and hand-off

`prototype/` (static, no build step):

- `nightjar.css`: tokens, shell, Tick, hold and palette.
- `nightjar.js`: theme, reduced motion, runtime clocks, Tick and hold. Deadlines are computed from the browser clock.
- `index.html`: the landing replay plus the shift log.
- `console.html`: the watch with INC-0412, the baton tracks, the approval hold, a draft notice and ⌘K.
- `brief.html`: the morning brief (dawn / night edition).
- `shoot.mjs`: reproduces `shots/` with playwright-core and system Chrome at 1440×900 @2×. The `Date` is frozen to 2025-11-20 09:18 Europe/Brussels, and the script checks overflow and that no 2026 date appears.

The only external dependency is Google Fonts (Geist, Geist Mono). The visible text passes the forbidden-word scan.

**Svelte components to build:**
- shell: `ClockBar`, `ClockCell` (Tick + Drop + elapsed rule), `Tiles` (split/focus/zoom, layout presets), `StatusLine`, `CommandPalette`;
- console: `EventRiver` (minute groups + fold), `IncidentHeader`, `StageRail`, `BatonTimeline` (lane tracks, SVG links, bead, Replay controller), `StepDrawer`, `ApprovalCard`, `HoldButton`, `NoticeDraft`, `HuntTile`, `RhythmStrip`;
- brief: `BriefPage`, `DecisionBox`, `ClockLedger`, `SevenNights`.

Clock maths lives in one `clock.ts` (regime windows, business days with a US federal holiday list, timezone per regime). The UI never computes deadlines inline.

**Regulatory facts used (designed to support, not legal advice):**
- **NIS2 Art. 23:** early warning within 24 h of becoming aware, incident notification within 72 h, final report within one month of the notification.
- **SEC Form 8-K Item 1.05:** four business days after the registrant determines the incident is material.
- **SOCI Act Part 2B** (report to ASD's ACSC): 12 h for a critical incident with significant impact, 72 h for one with relevant impact.
- **Belgium's CSIRT/authority for NIS2:** CCB.
