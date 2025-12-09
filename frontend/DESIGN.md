# Nightjar SOC frontend design, v3: red light

> The design the web app is built to. v1 (a tiling console under a regulator clock bar) and v2 (the shift chart: a time axis on top, full-width bands, a tmux-style watch bar) are in this file's history. v3 keeps every feature, route, role, data flow and stream, and changes how the product looks, reads, navigates and moves.

Nightjar SOC is a managed security-operations platform for mid-size companies. It takes in security events around the clock, groups threats into incidents (Sigma rules, MITRE ATT&CK) and spots unusual behaviour. Three cooperating AI teammates (**Triage**, **Enrichment**, **Response**) investigate. When an incident is reportable, it starts the regulator's clock (EU NIS2, US SEC Form 8-K Item 1.05, Australia SOCI) and drafts the notice. **A person approves every response action.** Executives get a plain-language morning brief.

- **Demo company:** **Halden Logistics**, with an EU distribution centre in Antwerp (NIS2, GDPR), a US-listed parent in Memphis (SEC Form 8-K) and a freight network run from Melbourne (SOCI).
- **Stack:** SvelteKit + TypeScript + Tailwind 4.1 with **Skeleton** (`@skeletonlabs/skeleton` 3.2.2, `@skeletonlabs/skeleton-svelte` 1.5.3, Zag.js underneath) on a rebuilt `nightjar` theme. Motion uses Svelte built-ins only (`svelte/transition`, `svelte/animate`, `Spring` / `Tween`, `prefersReducedMotion`) and CSS.
- **Fonts** (self-hosted through Fontsource): **Big Shoulders Display** for headings, **Manrope** for the interface and reading, **Fira Code** for clocks, logs and IDs.
- **Copy date:** written as of late 2025. Every time-sensitive value is computed at runtime from one clock helper (§12).

---

## 1. Why v3

v2 fixed the skeleton (the night became the x-axis) but kept the old surface: blue-black with mint teal, small mono labels on every rule, hairline tiles, and a top strip plus a bottom strip on every screen. Next to the other products, the type read like everyone else's: a sentence-case grotesk heading over a neutral body. Strangers saw the same product as v1.

v3 starts from the room instead of the chart: **an operations room on night watch, lit red so the people in it keep their night vision.** Ships' bridges, observatories and control rooms dim to red at night. The IMO display standard asks for "light foreground information on a dark non-reflecting background" at night, and the IHO notes that under red bridge light some colours become hard to tell apart. That last point is a design rule here: **state is carried by shape, words and brightness, never by hue alone.**

Three objects from real night watches give the product its parts:

| Object | Where it comes from | What it becomes |
|---|---|---|
| **The annunciator** | Back-lit, engraved alarm windows on a control-room panel; ISA-18.1 sequences make a new alarm flash until it is acknowledged | The shell: a **station plate** with the page name in tall stencil capitals, beside a **lamp panel** of windows that are the navigation. A window lights when you are there and flashes once, then glows, when something there needs a person |
| **The watchman's dial** | The Detex Newman watchclock (1927): a paper dial turned by the clock; each station a guard visited printed a mark, so a complete round read as a continuous band and a missed station showed as a gap | The home and the landing: **the watch dial**, a 24-hour face. The shift is the lit sector, the hand is now, each incident is a line that moves round its stage ring and steps inward as it progresses, and the regulator clocks sit on the rim ahead of the hand |
| **The strip bay** | Air-traffic flight progress strips: one strip per flight in a holder, racked in bays; where a strip sits is information | Every register and list: **strips** with a coloured holder edge, boxed fields and stencil column heads, racked in bays |

And one document: the **night orders** a captain leaves for the officer of the watch, answered in the morning by the watch's report. The CISO's brief is that report.

---

## 2. References

Re-checked for v3. Domain references first, then the SOC tools the workflows are built on (unchanged from v2), then type and gallery references.

| # | Reference | What we take |
|---|---|---|
| 1 | IMO MSC.191(79), performance standards for navigational displays: https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.191(79).pdf | Day, dusk and night presentations; at night, light foreground on a dark, non-reflecting background. → Dark is the default "night" theme; the light theme is the day presentation, not an inverted night |
| 2 | IHO CSPCWG, colours under bridge lights: https://docs.iho.int/mtg_docs/com_wg/CSPCWG/CSPCWG7/CSPCWG7-09.2A_Colours_under_bridge_lights.pdf | Under red light, colours become hard to tell apart. → Status by glyph, word and luminance; colour only reinforces |
| 3 | Watchclock (Wikipedia) and the Detex history: https://en.wikipedia.org/wiki/Watchclock · https://www.watchclocks.org/Types/Collection/history/history.html | The dial turned by the clock, stations registered as marks, gaps show a missed round. → The watch dial: the shift as a lit sector, incidents registered where they happened |
| 4 | Annunciator panel: https://en.wikipedia.org/wiki/Annunciator_panel · ISA-18.1 sequences as described in vendor manuals (e.g. GE UR front panel: https://www.manualslib.com/manual/1337027/Ge-C70.html?page=150) | Engraved windows in a grid; a new alarm flashes until acknowledged, then glows steady. → The lamp panel: navigation windows that light and flash once |
| 5 | Flight progress strips: https://en.wikipedia.org/wiki/Flight_progress_strip · SKYbrary: https://skybrary.aero/index.php/Flight_Progress_Strips | One strip per flight, boxed fields, holders racked in bays; the position is information. → Strip bays for every register; the incident header is one long strip |
| 6 | ISA-101 high-performance HMI ("Going gray"): https://control.com/technical-articles/going-gray/ | A calm, mostly neutral display where colour is reserved for what is abnormal. → Warm neutrals for routine; red only for now, focus and the one action; the brightest lamp is what needs a person |
| 7 | Big Shoulders (Patric King for the Chicago Design System): https://fontsource.org/fonts/big-shoulders-display/about | Condensed American Gothic capitals; the stencil cut of the family draws on railway and transport lettering. → Tall, tracked stencil-board labels for every heading |
| 8 | Google SecOps case playback: https://cloud.google.com/chronicle/docs/soar/investigate/working-with-cases/explore-entities-and-alerts-investigation | Step through a case's alerts in order. → Replay on the incident page |
| 9 | Microsoft Defender attack story: https://learn.microsoft.com/en-us/defender-xdr/investigate-incidents | One incident, one story, alerts in time order. → The incident strip and its log |
| 10 | Splunk ES response plans: https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.7/mission-control/respond-to-investigations-with-response-plans-in-splunk-enterprise-security | Phased response. → The stage rings of the dial and the stage boxes on the strip |
| 11 | Google SRE, managing incidents and handover: https://sre.google/sre-book/managing-incidents/ · SecOps shift handoffs: https://cloud.google.com/blog/products/identity-security/how-to-nail-smooth-shift-handoffs-in-secops | An explicit hand-off in fixed sections. → The four numbered readouts around the dial |
| 12 | NIST SP 800-61r3: https://csrc.nist.gov/pubs/sp/800/61/r3/final | People keep high-impact response actions. → Hold to authorise, and "waiting on you" first on every incident |
| 13 | Datadog Bits Security Analyst: https://docs.datadoghq.com/bits_ai/bits_security_analyst | A plain verdict with the evidence under it. → "Likely real: 94 in 100", then why and looked at |
| 14 | Panther simple detection builder: https://docs.panther.com/detections/rules/simple-detection-builder | Detections readable as forms. → Rules as sentences |
| 15 | Tines case requirements: https://www.tines.com/blog/what-s-new-in-tines-may-2025-edition/ | Preview unmet requirements before you commit. → The notice's pre-send checklist |
| 16 | Hold to confirm: https://motion.dev/tutorials/js-hold-to-confirm | A held press beats a dialog. → Hold to authorise, 1.2 s |
| 17 | Military message forms (the Joint Message Form, DD 173) and the date-time group: https://navyadvancement.tpub.com/14145/css/Filing-Procedures-78.htm · https://informationtechniciantraining.tpub.com/14222/css/Common-Message-Elements-57.htm | A boxed header (to, from, precedence, date-time group) above numbered text. → The notice draft as a signal form |
| 18 | Awwwards colour filter for dark reds: https://www.awwwards.com/websites/%23BF2615/ | Dark sites that let one saturated red carry the page, with condensed display type. → One red, used as light, on warm black |

**Avoided:** neon on black, glowing world maps, scanlines, glitch, fake-hacker green, the radar "sonar ping" cliché as decoration (the dial's hand moves only with real time or the replay), and every shell in `DESIGN_DIRECTIONS.md`.

---

## 3. The shell: station plate + lamp panel

```
┌──────────────────────────────┬──────────────────────────────────────────────────┬─────────────────────────┐
│ ◗ NIGHTJAR SOC · HALDEN      │ ┌─────────┐┌─────────┐┌───────────┐┌─────────┐    │ 10:30:12  CET          │
│                              │ │ WATCH  2││  HUNT   ││ BEHAVIOUR ││  RULES  │    │ THU 20 NOV · FORENOON  │
│ THE WATCH                    │ └─────────┘└─────────┘└───────────┘└─────────┘    │ ○ NIS2 · NJ-1036 16:48 │
│ 4 open · 1 waiting on you    │ ┌─────────┐┌─────────┐┌───────────┐┌─────────┐    │   +3 clocks            │
│                              │ │ ASSETS  ││ PEOPLE  ││    LOG    ││  BRIEF  │    │ [⌘K find]   (MB)       │
└──────────────────────────────┴──────────────────────────────────────────────────┴─────────────────────────┘
  page skeleton below (varies by screen type, §4); the page scrolls, the head scrolls with it
```

- **Station plate (left).** The brand line, then the page name as the page's `h1` in Big Shoulders Display at 64 px, in capitals, tracked, followed by a one-line summary in Manrope. The plate is the heading; pages do not repeat it.
- **Lamp panel (centre).** Eight engraved windows in a 4 × 2 grid, not a tab row: Watch, Hunt, Behaviour, Rules, Assets, People, Log, Brief (Alt+1 … Alt+8 as before; windows a role cannot open are not drawn). States:
  - *off*: engraved label, dim;
  - *lit*: you are here; the window glows red from inside and the label is bright, with `aria-current="page"`;
  - *alarm*: something there needs a person (Watch: approvals waiting; Rules: noisy rules). A count in Fira Code sits in the window's corner with a ▲; a new alarm flashes twice, then glows (ISA-18.1 style). Reduced motion: no flash.
- **Chronometer (right).** The org's local time (Fira Code, rolling seconds), the date and the **watch** it falls in (First 20–00, Middle 00–04, Morning 04–08, Forenoon 08–12, Afternoon 12–16, Dog 16–20). Under it the most urgent regulator clock in glyph + words; "+3 clocks" opens all of them in a popover. A new clock **drops** into this slot. Then ⌘K ("Find") and you (Avatar → role, day/night presentation, confirm-by-typing, settings, sign out).
- **No bar on the bottom, no side rail, no right panel.** Nothing is pinned to an edge; the whole page, head included, scrolls as one sheet.
- **Phone (390 px):** one row: brand mark, chronometer, **Panel** button; then the station name at 40 px. Panel drops the lamp windows (2 × 4), the clocks and you from the top as a Skeleton modal.
- **Detail** opens as Skeleton modals: a **sheet** that rises from the bottom (a step's evidence, an asset, a person) or a **dialog** that drops from the head (⌘K, a decision, a delete).

The public pages (landing, sign-in, invitation) use the same plate with a reduced panel.

---

## 4. Page skeletons, per screen type

No screen uses "top bar + centre + right panel" or a card grid. Each skeleton comes from how that screen is used.

| Screen | Skeleton | Layout |
|---|---|---|
| **The watch** `/watch` | **Scope with four readouts** | The watch dial in the centre (up to 600 px). The turnover's four numbered readouts sit at its corners, like the data blocks around a radar scope: 1 top-left, 2 top-right, 3 bottom-left, 4 bottom-right. Responders and admins: 1 waiting on you · 2 clocks to watch · 3 intake, live · 4 since you last looked. Analysts: 1 worth a hunt. Auditors: 1 the activity chain, 3 evidence. Under 1180 px the dial goes on top and the readouts sit 2 × 2; on phones they stack as an accordion |
| **Incident** `/incidents/[id]` | **Strip, orders, log** | One long **flight strip** across the page: a holder edge for severity, the reference in Fira Code, the title, then boxed fields (stage rail · people · machines · clocks · ATT&CK). Under it, **orders**: "waiting on you" as lit plates side by side, each with Hold to authorise, plus open regulator assessments. Then the record as an engraved index (Skeleton Tabs): *what happened, and why* (the replay as a watch log with the four teammate/person columns) · clocks and notices · actions decided · service desk · evidence |
| **Notice draft** | **Signal form** | A boxed header like a naval signal form (TO · FROM · PRECEDENCE · DUE · REF), then numbered paragraphs that are the notice's fields. A **pre-send checklist** runs down the left: each required field ✓ or ◐ with "confirm …", linking to its paragraph. The evidence it cites is a strip bay below; `[E-xx]` scrolls to and lights its strip |
| **Morning brief** `/brief` | **The morning report** | Light by default, under a "first light" horizon (a band that runs from the night's red into bone). A stencil headline across the full width, then a three-column report: the decision plate (the one boxed thing on the page) · the night (a compact, read-only watch dial) · clocks, approved overnight, on watch now. Then "overnight in plain words" and seven nights |
| **Weekly report** | **Report sheets** | The same report grammar for a week: a headline, the week as seven narrow dial-less columns (one per night), then numbered sections |
| **Registers**: assets, people, rules, activity, saved searches, members | **Strip bay** | Strips racked in a bay: holder edge (state), boxed fields, stencil column heads, a filter rail of lamp-style segments above. A strip opens as a bottom sheet (asset, person) or a page (rule). The activity log is a **log book**: strips joined by a chain mark, verified as a whole |
| **Hunt** | **Plot** | A wide command line ("Ask in plain words") with the filters as chips beneath; then a bar plot of the hours and a strip bay of what matched. Save as a named search |
| **Behaviour** | **Tapes** | One tape per person or machine: their normal week as a rhythm strip, with the unusual hours punched through and marked ◇ |
| **Rule** `/rules/[id]` | **Rule plate** | The rule as a sentence on an engraved plate (When … Unless …), then its noise over 14 nights as a tape, then the source one keystroke away |
| **Settings** | **Plates** | Regulator contacts and members as labelled plates with strips inside |
| **Sign-in** | **The watch bill** | Left: the idle dial showing the time. Right: "YOU TAKE THE WATCH AT 10:30" and the watch bill: the demo roles as rows of a roster (role, who, what they do, **Take**), then your own account |
| **Landing** `/` | **Red-light replay** | The dial replays one night at Halden Logistics, large, beside the night log and the chronometer; the hold box arrives in the log; the stencil headline lands last. Below: **the round** (22:00 · 01:30 · 03:12 · 08:00 as four engraved stations), the clocks it keeps as a strip bay, and a first-light footer |

---

## 5. Type

| Role | Face | Setting |
|---|---|---|
| Display | Big Shoulders Display 800 | 120–168 px landing, 0.9 line height, capitals, +0.02em |
| Station name (h1) | Big Shoulders Display 800 | 64 px (40 px on phones), 0.92, capitals, +0.03em |
| Plate heading (h2) | Big Shoulders Display 700 | 22–28 px, capitals, +0.08em |
| Label / engraving | Big Shoulders Display 700 | 13–15 px, capitals, +0.16em (lamp windows, column heads, buttons) |
| Body | Manrope 400–600 | 15 px / 1.5 console, 17–19 px / 1.6 brief |
| Strong UI | Manrope 700 | titles of strips, approval titles |
| Clocks, logs, IDs | Fira Code 400–500, `tnum` | 11.5–14 px; 40–96 px chronometer and replay clock |

**The heading treatment:** headings are never sentence case and never a light weight. They are tall, condensed capitals, tracked out like letters painted through a stencil onto a bulkhead or a lamp window. A short red **index bar** (4 × 18 px) sits before plate headings; numbered sections put their number in a boxed lamp. Fira Code appears only where a machine wrote it or a clock counts it (times, references, hosts, hashes, event lines); everything a person reads is Manrope.

---

## 6. Palette

Night-vision red, hue ≈ 5°, is the only accent. Teal and lavender are retired.

### Night (default)

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `bg` | `#120A08` | the room | |
| `plate` | `#1A100D` | plates, strips, readouts | |
| `raised` | `#231512` | hover, chips, lamp windows | |
| `sunk` | `#0C0605` | inputs, the dial face | |
| `rule` / `rule-2` | `#33201B` / `#553630` | hairlines / control borders | |
| `ink` | `#F6E9E3` | text | 15.7 : 1 on plate |
| `ink-2` | `#D9C3BA` | secondary text | 11.1 : 1 |
| `dim` | `#B39A91` | meta | 7.1 : 1 |
| `faint` | `#A1857C` | timestamps, quiet labels | 5.5 : 1 |
| `acc` | `#FF5340` | now, live, focus, lit lamps, the primary action | 5.9 : 1 |
| `acc-hi` | `#FF8270` | accent text on raised surfaces | 7.3 : 1 |
| `acc-ink` | `#1A0805` | text on red fills | 6.3 : 1 |
| `person` | `#FFE4DA` | ‖ waiting for a person, ◆ decisions (the brightest lamp) | 14.3 : 1 |
| `soon` | `#F2B04A` | ◐ due soon, ◇ unusual, missing fields, noisy | 9.9 : 1 |
| `ok` | `#86CFA2` | ✓ contained, sent, authorised | 10.2 : 1 |

### Day (light)

| Token | Hex | Contrast |
|---|---|---|
| `bg` / `plate` / `raised` / `sunk` | `#F3EEE8` / `#FBF8F4` / `#ECE5DE` / `#E9E2DA` | |
| `rule` / `rule-2` | `#E0D6CD` / `#C2B2A6` | |
| `ink` / `ink-2` / `dim` / `faint` | `#1D1311` / `#47362F` / `#6A5750` / `#705B53` | 17.2 / 10.8 / 6.4 / 5.6 on plate |
| `acc` | `#B3241A` | 6.2 : 1 on plate, 5.7 on bg |
| `person` | `#1D1311` (heaviest weight + ‖ / ◆) | |
| `soon` / `ok` | `#8A5200` / `#1E6A44` | 6.0 / 6.2 |

The brief uses the day tokens by default ("first light"), with the night tokens as the night edition. Its horizon band runs `#2A0E09 → #8E2A1C → #E9B49C → #F3EEE8`.

### Status: never colour alone

| State | Shape + words | Luminance / colour |
|---|---|---|
| Clock on track | `○ On track` | ink-2 |
| Due soon | `◐ Due soon` | soon |
| Overdue | `● OVERDUE` in an inverted block | acc fill, acc-ink text |
| Not started, needs a decision | `◆ Materiality decision` + dashed underline | person |
| Sent | `✓ Sent` | ok |
| Waiting for a person | `‖ Awaiting your approval` in a lit lamp window | person |
| Authorised | `✓ Authorised · time · by name` stamp | ok |
| Declined | `✕ Declined · reason` | dim |
| Severity | `▮▮▮▮ CRITICAL` inverted block · `▮▮▮▯ HIGH` · `▮▮▯▯ MEDIUM` · `▮▯▯▯ LOW` | block / acc / soon / dim |
| Unusual behaviour | `◇` + words | soon |
| Missing field | `◐ confirm …` | soon |

Every pair is checked to WCAG AA in both themes by the crawl (§12).

---

## 7. Components: Skeleton, rebuilt

The `nightjar` Skeleton theme is regenerated from §6: `primary` is the red (300 = night `#FF8270`, 500 = `#FF5340`, 700 = day `#B3241A`), `secondary` the lamp neutrals, `tertiary` warm greys, `success` / `warning` / `error` the status set, `surface` from bone `#FBF8F4` to `#0C0605`.

| Part | Shape |
|---|---|
| Radius | 2 px on controls, 0 on plates, strips and sheets; the dial and the hold ring are the only circles |
| Borders | 1 px rules; plates have a 2 px top edge in `rule-2`; the lit state is an inner red glow, not a border |
| Density | 40 px strips, 36 px controls, 44 px lamp windows |
| Buttons (`btn`) | Big Shoulders 700 capitals, +0.12em. Primary = a lit lamp (red fill, dark text); secondary = engraved (rule border, ink text); quiet = text with an underline |
| Segment (`Choice`) | A row of small lamp windows; the selected one is lit |
| Tabs | An engraved index: capitals, the active tab lit from below with a red bar |
| Badges / chips | Engraved tags with a 1 px rule and a Fira Code count |
| Tables (`table`) | Strip bays: each row a strip with a 4 px holder edge |
| Modal (`Sheet`) | Sheets rise from the bottom with a red top edge; dialogs drop from the head |
| Focus | 2 px `acc` outline, 2 px offset, always visible on keyboard focus |
| Corner ticks | The dial, the hold box and the decision plate carry four L-shaped corner ticks, like a bezel |
| Logo | The nightjar's eye inside a dial ring: an arc, a dot and one tick at the top |

Components used: Modal, Tabs, Segment, Accordion, Popover, Tooltip, Toaster, Avatar, Switch, Progress, ProgressRing (the hold ring) and Pagination, plus the `btn`, `badge`, `chip`, `input` and `table` utilities with presets.

---

## 8. The watch dial

- A 24-hour face, midnight at the top, read clockwise. The six watches are labelled on the rim (FIRST, MIDDLE, MORNING, FORENOON, AFTERNOON, DOG) with the hours between.
- **The shift** (the 18:00 before the last night, or 12 hours back, to now) is the lit sector; the rest of the day is unlit. Night hours (20:00–07:00) are shaded on the face; the 08:00 and 20:00 handovers are ruled.
- **Rings**, from the rim inward: intake (hourly bars standing out from the ring), triage, investigate, contain, report. **Each incident is one line**: it runs clockwise along its stage ring and steps inward when its log records the next stage, the polar form of the dispatcher's timetable graph. Regulator notices mark the line (✉) without moving it.
- **The hand** is now. A soft wedge of persistence trails it. Open incidents end at the hand.
- **The rim ahead of the hand** carries the regulator deadlines due in the next 24 hours as ▼ ticks with their names.
- **The centre** holds the time (Fira Code), the watch name and "4 open · 1 waiting".
- Every line is a link with a spoken label ("NJ-1038 …: opened 02:16, now in contain, waiting for a person"); the figure's label summarises the rings. On phones the dial is drawn at the screen's width and the lines are also listed by stage in an accordion.

---

## 9. Motion: "red light"

Svelte built-ins and CSS only; transform and opacity, except the hold ring.

| Name | What moves | Timing | Reduced motion |
|---|---|---|---|
| **Sweep** | the dial's hand rotates to now; the persistence wedge follows | each minute, 900 ms ease-out; the landing's replay runs it continuously | the hand jumps |
| **Register** | a new mark on the dial punches in (scale 1.8 → 1, opacity) with one ring | 420 ms `cubic-bezier(.2,1.3,.35,1)` | appears |
| **Lamp** | an alarm window flashes twice, then glows steady | 2 × 160 ms, then steady | steady glow |
| **Rack** | strips slide into a bay 8 px from the left, 24 ms apart (first 12) | 260 ms | appear |
| **Tick** | rolling digits on the chronometer and the clocks | 420 ms; 140 ms during replay | swap |
| **Drop** | a new clock drops into the chronometer slot | 620 ms back-out | appears; announced |
| **Hold** | the ring fills for 1.2 s; release springs back; completion stamps | 1200 ms linear; 420 ms stamp | numeric %, the 1.2 s still applies |
| **Step** | an incident line grows along its ring from where it opened | 900 ms | drawn whole |
| **First light** | watch → brief: the brief rises behind a horizon wipe | 700 ms via View Transitions | 150 ms fade |

Never: scanlines, glitch, parallax, scroll-jacking, decorative radar pings, typing cursors.

---

## 10. Signature interactions

1. **Replay the night / an incident.** The landing replays a night on the dial; any incident replays step by step through its watch log.
2. **Hold to authorise.** Pointer, Space or Enter for 1.2 s; early release cancels; or confirm by typing the name.
3. **The lamp panel answers.** A window lights where you are and flashes once when something there needs a person, so the navigation is also the alarm panel.

---

## 11. Copy voice

Terse, operational, time-stamped and calm. Sentence case in body text; capitals only in the stencil headings and labels. No exclamation marks, no emoji.

- CTAs: Take the watch · Replay the night · Hold to authorise · Decline with a reason · Ask Response why · Open the draft · Review and submit · Record the decision · Save as… · Verify the log.
- Teammates are Triage, Enrichment and Response; together "the watch" or "three AI teammates".
- Compliance: "designed to support NIS2, SEC Form 8-K Item 1.05 and SOCI reporting". Never "compliant", "certified" or "files for you". Demo submissions read "Recorded as submitted (demo)".
- Forbidden in rendered text: RAG, retrieval, embeddings, vector, rerank, LLM, model or provider names, LiteLLM, prompt, tokens, SQL, architecture, pipeline, agent internals, A2A, MCP, latency, fallback, cache; also "agent", "agentic", "AI-powered", "autonomous", "smart", "magic", "algorithm", "SOAR". Analyst screens may show ATT&CK IDs, Sigma, host names and event fields.

---

## 12. Build and checks

- **Tokens:** `src/app.css` (§6 as CSS variables, night and day) and `src/lib/theme/nightjar.css` (the Skeleton theme from the same values).
- **Shell:** `StationHead` (plate, lamp panel, chronometer, you), `LampPanel`, `Chronometer`, `CommandPalette`, `Logo`, `Tick` / `Digit`.
- **Primitives:** `Plate` (titled section, numbered or not, corner ticks optional), `Choice` (Segment), `Sheet` (Modal), `Strip` helpers in CSS (`.bay`, `.strip`).
- **Console:** `WatchDial` (polar layout from `util/shift.ts`: `dialAngle`, `dialPath`), `Turnover` (the four readouts around a centre snippet), `IncidentStrip`, `BatonTimeline` (the watch log), `ApprovalCard`, `HoldButton`, `ClockList`, `EventRiver`, `HuntPanel`, `BehaviourPanel`, `ActivityPanel`, `EvidenceTile`, `StepDrawer`, `DecisionSheet`.
- **Clock:** one `src/lib/time/clock.ts` (`NIGHTJAR_DEMO_TODAY` on the server; the browser's `Date` is shifted to Thursday 20 November 2025 for screenshots by an init script that replaces only `Date`).
- **Checks:** svelte-check, ESLint, Vitest, the production build, and a browser crawl of every route for every role in both themes at 1440 px and 390 px (overflow, WCAG AA text contrast, forbidden words, console errors).

**Regulatory facts used (designed to support, not legal advice):** NIS2 Art. 23 early warning within 24 h of becoming aware, notification within 72 h, final report within a month; SEC Form 8-K Item 1.05 four business days after the materiality determination; SOCI Part 2B 12 h (significant impact) and 72 h (relevant impact) to ASD's ACSC; Belgium's NIS2 authority is the CCB.
