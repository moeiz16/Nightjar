# ADR 0001: NATS JetStream as the event backbone

**Status:** accepted

## Context
Security events arrive in bursts (a password spray, a backup-software rollout) and must flow through several
independent stages (normalise, store, detect, profile behaviour, score, correlate, investigate) without losing
messages, without one slow stage stalling the others, and with an honest answer to senders when the system is
full. The product runs on an 8 GB laptop with no Docker and no Java, so Kafka/Redpanda clusters are out. Postgres
queues would couple throughput to the app database; an in-process queue loses work on restart.

## Decision
Use **NATS JetStream**: the single `nats-server` binary (Apache-2.0, pinned v2.15.3, downloaded with checksum
verification into git-ignored `.tools/` by `make tools`) and the official `nats-py` 2.16 client. Streams per stage
(`NJ_RAW`, `NJ_EVENTS`, `NJ_DETECTIONS`, `NJ_INCIDENTS`, `NJ_WORK`, `NJ_DLQ`) with durable pull consumers; the
full design is API contract Part 4. Tests and single-process demos use an in-process bus with the same interface
(`BUS=memory`).

Verified on Python 3.13, macOS arm64 with `scripts/smoke/nats_smoke.py` (real server, temporary store):
- publish with stream acks, and duplicate suppression by `Nats-Msg-Id` within the duplicate window;
- a durable pull consumer resumes after the acked position across unsubscribe/re-subscribe;
- `nak()` redelivers immediately; an unacked message is redelivered after `ack_wait`;
- after `max_deliver` the `MAX_DELIVERIES` advisory fires and the original (fetched by stream sequence) is copied
  to a DLQ stream with provenance headers;
- a byte-limited stream with `discard=new` rejects publishes when full (the API's `503 intake_full`);
- `consumer_info` exposes `num_pending`/`num_ack_pending` for lag reporting;
- nats-server resident memory: **22.8 MB**.

## Consequences
- Backpressure is explicit at the edge (discard-new on the raw stream) and bounded inside (`max_ack_pending`).
- Every handler must be idempotent (redelivery is normal); ids and correlation keys make that cheap.
- Pull consumers: drain/close must unsubscribe pull subscriptions first (draining a pull subscription can time
  out; the smoke check closes instead).
- One more process to run (`make nats`, included in `make dev`); `/health/ready` and the status line report it.
- Production path: the same subjects on a NATS cluster with replicas; no code change.
