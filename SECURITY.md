# Security

Pharos 0.1.x is early alpha. Only the latest source revision receives fixes during this phase.

Report vulnerabilities privately through [GitHub security advisories](https://github.com/zvckster/pharos/security/advisories/new). Do not publish exploit details or real secrets in public issues.

## Trust boundaries

Configuration is trusted: a command detector can execute arbitrary programs with the Pharos user's privileges. Restrict configuration write access. Do not accept catalogues from untrusted users without reviewing them. Release sources and notification endpoints make outbound network requests; configuration can direct them to internal hosts, so this is not a multi-tenant service or an SSRF sandbox.

Docker daemon access is effectively host administration access. A read-only socket bind mount does not restrict Docker API operations. The default container avoids mounting it; use a dedicated native installation for host detection or an independently configured API proxy.

Release APIs are untrusted input. Pharos limits responses, validates HTTPS source URLs, rejects downgrade redirects, removes authentication on cross-host redirects and never runs downloaded release code. Socket timeouts are not whole-operation deadlines; production deployments should impose a run deadline as the systemd example does.

Store credentials in dedicated environment variables and restrict service configuration files. Do not commit `.env`, personal catalogues, raw Docker inventory or state. Upstream release credentials and notification credentials are not stored in reports. Error messages intentionally avoid remote response bodies and authenticated URLs.

Pharos does not detect security fixes shipped solely as image rebuilds. It does not validate release signatures, scan for vulnerabilities, or upgrade applications. Use it alongside your patch management process.
