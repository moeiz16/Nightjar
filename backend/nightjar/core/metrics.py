"""Prometheus metrics (exposed at `/metrics`). Pipeline and agent processes expose their own registries on the
same names via `nightjar.core.metrics` so a single scrape config covers every process."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("nightjar_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "nightjar_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
RATE_LIMITED = Counter("nightjar_rate_limited_total", "Requests rejected by rate limiting", ["bucket"])
AUDIT_APPENDS = Counter("nightjar_audit_events_total", "Audit events appended", ["action"])
SSE_CLIENTS = Gauge("nightjar_sse_clients", "Open live-update streams")

# Pipeline (builder B increments these; declared here so names stay stable)
EVENTS_INGESTED = Counter("nightjar_events_ingested_total", "Raw events accepted", ["source"])
EVENTS_REJECTED = Counter("nightjar_events_rejected_total", "Raw events rejected", ["reason"])
DETECTIONS = Counter("nightjar_detections_total", "Detections emitted", ["source", "severity"])
DLQ_MESSAGES = Counter("nightjar_dlq_messages_total", "Messages moved to the dead-letter stream", ["stream"])
CONSUMER_LAG = Gauge("nightjar_consumer_pending", "Messages pending per durable consumer", ["consumer"])
STAGE_LATENCY = Histogram(
    "nightjar_stage_seconds",
    "Per-stage processing time",
    ["stage"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 15, 60),
)
ALERT_TO_TRIAGE = Histogram(
    "nightjar_alert_to_triage_seconds",
    "First detection of an incident to a recorded triage verdict (NFR: under 60 s)",
    buckets=(1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 300),
)
AGENT_STEPS = Counter("nightjar_agent_steps_total", "Agent steps recorded", ["agent", "kind"])
TOOL_DENIALS = Counter("nightjar_tool_denials_total", "Tool calls refused (scope or approval)", ["agent", "tool"])
INJECTION_FLAGS = Counter("nightjar_injection_flags_total", "Untrusted fields flagged as instruction-like", ["field"])
