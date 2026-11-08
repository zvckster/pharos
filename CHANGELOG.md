# Changelog

## 0.1.1 — 2026-11-08 (alpha)

- Fix abbreviated prereleases, five-part version truncation and stable build metadata handling.
- Suppress releases below the historical maximum after upstream rollbacks.
- Validate nested state and configuration before side effects; retain original files on failure.
- Snapshot notification target IDs, preserving deliveries when a channel disappears.
- Include upstream source identity in notification IDs to prevent cross-source collisions.
- Bound combined subprocess output while streaming, reap process groups on timeout, and read stderr-only versions.
- Close HTTP error response bodies, including conditional-request responses.
- Handle nullable release links and clock rollback in cache expiry.
- Add controlled, credential-free check error reasons to reports.
- Move the production container to Python 3.15; extend stable CI through 3.15 and add experimental Python 3.16 checks.
- Expand regression and fault-injection coverage, including deterministic version property checks.

Upgrades from 0.1.0 establish a quiet parsing-policy baseline while preserving the outbox. See the README.

## 0.1.0 — 2026-11-08 (alpha)

Initial standalone alpha extracted from a personal release-monitoring workflow.

- GitHub, GitLab, Forgejo and generic JSON release sources.
- Native dpkg/rpm, Docker metadata, static and command version detectors.
- Built-in ntfy, TLS email, Gotify and JSON webhook notifications.
- Quiet baselines, numeric stable-version filtering and rollback deduplication.
- Durable grouped notification outbox with per-channel retries.
- Dry runs, JSON reports, degraded-check alerts and process locking.
- Python package, container, Compose and systemd deployment examples.
