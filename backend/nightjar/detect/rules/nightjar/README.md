# Nightjar rules

Nightjar's own detection rules (MIT, this repository) for log sources the SigmaHQ set covers thinly:
identity provider prompts, mailbox rules, cloud access keys, remote-access gateways, file-sync tools and
instruction-like text in log fields. Field names are Nightjar's ECS-like names (see `detect/flatten.py`).
Base rules at level `informational` only feed correlation rules and never alert on their own.
