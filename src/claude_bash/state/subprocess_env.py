"""Port of subprocessEnv.ts.

Returns a copy of the environment for spawned children. When
``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`` is truthy (the GitHub Actions / untrusted-
content case), strips secrets that a prompt-injection could otherwise exfiltrate
via shell expansion.
"""

from __future__ import annotations

import os

# Vars stripped from subprocess envs when scrubbing is enabled. GITHUB_TOKEN /
# GH_TOKEN are intentionally NOT scrubbed (job-scoped, needed by wrapper scripts).
GHA_SUBPROCESS_SCRUB = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "ACTIONS_RUNTIME_TOKEN",
    "ACTIONS_RUNTIME_URL",
    "ALL_INPUTS",
    "OVERRIDE_GITHUB_TOKEN",
    "DEFAULT_WORKFLOW_TOKEN",
    "SSH_SIGNING_KEY",
)

_TRUTHY = {"1", "true", "yes", "on"}


def is_env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    if not is_env_truthy(env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB")):
        return env
    for key in GHA_SUBPROCESS_SCRUB:
        env.pop(key, None)
        env.pop(f"INPUT_{key}", None)
    return env
