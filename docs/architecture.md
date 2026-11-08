# Runtime decision

Pharos remains a Python application for the 0.1.1 hardening release. The production container uses the latest stable Python branch, 3.15. Supported Python 3.11–3.15 versions are tested in CI; Python 3.16 development builds are an additional experimental compatibility job. Development Python is not the production default.

## Why keep Python for this release?

The audit found defects in version classification, historical release comparison, state validation, subprocess handling and notification acknowledgement policy. Those rules require explicit tests in any implementation language. They have been corrected with targeted regression tests rather than carried into a second implementation.

Pharos checks releases every six hours by default and performs network and subprocess I/O. No evidence from this audit demonstrates a CPU performance problem requiring a language migration. The package has no runtime Python dependencies and can be installed in a virtual environment or a container.

Go would offer useful standalone binaries, static typing, and straightforward context-based network deadlines. A future Go migration should preserve the current behavior tests, demonstrate a distribution or operational benefit, and include state compatibility tests. A rewrite is not part of 0.1.1.

## Boundaries that still matter

HTTP socket timeouts are not total run deadlines, particularly for DNS and slowly streamed responses. The systemd deployment imposes a whole-run deadline. The container daemon does not yet have a strict whole-run deadline: use an external supervisor or native systemd where that guarantee is required.

Notification delivery remains at least once. Crashing after the receiver accepts a notification but before acknowledgement reaches disk can duplicate that notification. Stable event IDs help receivers deduplicate retries; changing upstream sources produces distinct event IDs.

The catalogue is trusted executable configuration. Do not expose it to untrusted tenants. Release-only monitoring intentionally excludes image-only security rebuilds; it complements patch management.
