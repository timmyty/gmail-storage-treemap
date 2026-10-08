# Changelog

## 1.1.0 — 2026-10-08

Initial public release.

- Gmail metadata scanning and a storage treemap with sender, domain, year, location, and label-set grouping.
- Message list, search, minimum-size filters, Gmail links, local snapshots, CSV export, and MBOX import.
- Progress dialog with phase, message counts, percentage, elapsed time, cancellation, and quota pauses.
- Quota-unit-based request pacing, shared cooldowns, and retries for per-minute quota errors.
- Resume for incomplete snapshots, with account verification and reuse of collected message metadata.
- Synthetic demo data and automated regression tests.
