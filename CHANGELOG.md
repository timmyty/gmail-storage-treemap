# Changelog

## Unreleased — security hardening (2026-10-23)

- **Resume scan…** now asks for confirmation first. The prompt warns never to resume a snapshot made by someone else, and it shows the account, scan time and message count the snapshot holds.
- HTTPS API calls (Gmail and the Google token endpoint) now refuse redirects to a different host or to plain http, so a bearer token is never forwarded off the original server.
- The OAuth loopback callback drops stalled local connections after 10 seconds instead of blocking sign-in.
- CSV export also neutralises spreadsheet formulas in the Date column (relevant for hand-edited snapshots).

## 1.1.0 — 2026-10-08

Initial public release.

- Gmail metadata scanning and a storage treemap with sender, domain, year, location, and label-set grouping.
- Message list, search, minimum-size filters, Gmail links, local snapshots, CSV export, and MBOX import.
- Progress dialog with phase, message counts, percentage, elapsed time, cancellation, and quota pauses.
- Quota-unit-based request pacing, shared cooldowns, and retries for per-minute quota errors.
- Resume for incomplete snapshots, with account verification and reuse of collected message metadata.
- Synthetic demo data and automated regression tests.
