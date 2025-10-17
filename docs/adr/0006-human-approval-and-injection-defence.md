# ADR 0006: Human approval for response actions, and the prompt-injection defence

**Status:** accepted

## Context
Agents propose containment (isolate a server, disable an account, revoke a cloud key). A wrong action at 3 a.m.
can stop a business; an attacker who can write into a log field (a user agent, a file name, an email subject) can
try to talk an agent into acting. The NFRs say: no autonomous destructive action, least privilege, and a replayable
decision record.

## Decision
**Approval gate.**
1. Every action kind in `contracts.DESTRUCTIVE_ACTIONS` (including reversals) needs a human approval; non-destructive
   kinds (open ticket, notify owner, collect a triage package, watchlist) run immediately and are recorded.
2. A responder approves; a crown-jewel asset or a privileged identity also needs the CISO/executive (two-person
   rule). Nobody can be the only approver of an action they requested. Pending approvals expire after 24 hours.
3. When the last required approval lands, the API signs an **approval token** (HMAC over org, approval id,
   action, target, nonce, expiry; 60 minutes) and hands it to the response agent through the work queue.
4. The response tool server executes only if: the caller has scope `response:execute`; the token verifies for this
   exact org + action + target; the approval is `approved`; the nonce is consumed for the first time; the target is
   still one of the incident's entities. Every refusal is audited (`action.refused`) and logged as a guardrail step.
   The signing key never leaves the API.
5. The web app's "Hold to authorise" gesture (1.2 s) is the only UI path to approval.

**Injection defence** (untrusted = anything from logs, emails, files, intel, tickets):
1. Neutralise at intake (`safety.neutralise`): strip control, zero-width and bidi characters, cap length, flag
   instruction-like content (imperatives aimed at an assistant, role markers, tool-call syntax, external links,
   long base64). Flags are kept on the event and raise a low-severity detection.
2. Quote, never concatenate: prompts have a fixed instruction block; untrusted values only enter as escaped,
   labelled data blocks.
3. Code chooses tools and targets from the incident's entity sets; model output is schema-validated JSON with enum
   verdicts and action kinds; free text is display-only.
4. Proposals targeting anything outside the incident are rejected (`target_not_in_incident`).
5. The UI renders untrusted text as plain text.

## Verification and gates
Unit tests for token binding, expiry and tampering exist (`tests/test_core.py`); the MCP smoke shows scope refusal
and approval-gated execution. Eval gates (`make eval`): 0 destructive executions without approval across all
replays and the injection corpus; 0 out-of-scope tool calls; 0 proposals on non-incident targets; ≥ 95 % of the
injection corpus flagged; verdicts on injected twins equal those on clean twins.

## Consequences
- Containment is slower by the time a human takes to approve; the product measures and shows time-to-contain so
  the trade-off is visible.
- The model cannot be "clever" with tools; richer autonomous tool use would need a new ADR and the same gates.
