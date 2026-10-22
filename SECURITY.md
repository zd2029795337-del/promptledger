# Security policy

PromptLedger is an early-stage project. Security fixes target the current `main` branch. There is no formal support window or guaranteed response time.

For a suspected vulnerability, use the repository's **Security → Advisories → Report a vulnerability** action when GitHub private vulnerability reporting is available. If that action is unavailable, open a public issue containing only a request for a private reporting channel. Do not include exploit details, credentials, private prompts, or sensitive outputs in that issue. A maintainer can arrange a private channel before details are shared.

Include the affected commit or version, platform, a minimal sanitized reproduction, expected impact, and any proposed fix when reporting privately. Please allow time for assessment and a fix before publishing details.

Treat suites, fixtures, reports, and model responses as potentially sensitive. Live-provider requests can send prompts to a third party and may incur charges; review the chosen provider's settings before use. Keep keys in your local environment and never commit them. Review generated reports before sharing or uploading them as CI artifacts.

The bundled prompt-injection example checks a small illustrative response pattern. Passing it is not a security guarantee, and PromptLedger is not a sandbox for executing model-generated code.
