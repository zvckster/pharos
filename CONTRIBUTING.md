# Contributing to Pharos

Start with a bug report or a small pull request. Describe the release source, redacted configuration, expected result and actual result. Never attach notification credentials, private feed URLs, raw Docker inspect output or personal state files.

Use Python 3.11+ and run `python -m unittest discover -s tests -v`. Add behavioral tests for new providers and fixes, including prereleases, bad responses and version ordering. Use recorded public fixtures or synthetic data rather than live API calls in tests. No test should contact a real notification destination.

Keep source fetching, installed detection and notification delivery separate. Use argument arrays rather than shell execution. Do not add automatic upgrades or unsafe TLS bypasses. Follow the existing explicit configuration and quiet-baseline policy. Preserve state compatibility or add an explicit tested migration; silently discarding state loses deduplication and pending notifications.

For a new notification adapter, test Unicode, authentication, non-success responses and delivery retries. For an upstream API provider, document pagination limits, rate limits and supported version syntax.

Contributions are provided under the project's MIT license. No contributor license agreement is required.
