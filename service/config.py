"""All configuration comes from environment variables (12-factor). Secrets are only ever read from the environment — never from files
in the repo, never logged. See docs/configuration.md for the full table."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _bool(v: Optional[str], default: bool) -> bool:
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    provider: str = "rule"                  # rule (offline, deterministic) | openai_compat
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""                       # AGENT_API_KEY: model provider credential (secret)
    api_token: str = ""                     # AGENT_API_TOKEN: if set, every /v1 request must send 'Authorization: Bearer <token>' (secret)
    database_url: str = ""                  # DATABASE_URL: postgresql://… ; empty = in-memory store (lost on restart)
    workspace: str = "./workspace"          # root the file tools are confined to
    enable_file_tools: bool = True
    max_concurrent_runs: int = 4
    max_steps_cap: int = 20                 # a request may ask for fewer steps, never more
    max_objective_chars: int = 2000
    approval_timeout_s: float = 300.0       # an undecided approval is DENIED after this long (fail closed)
    log_level: str = "INFO"

    @staticmethod
    def from_env(env=os.environ) -> "Settings":
        d = Settings()
        return Settings(
            provider=env.get("AGENT_PROVIDER", d.provider), model=env.get("AGENT_MODEL", d.model), base_url=env.get("AGENT_BASE_URL", d.base_url),
            api_key=env.get("AGENT_API_KEY", ""), api_token=env.get("AGENT_API_TOKEN", ""), database_url=env.get("DATABASE_URL", ""),
            workspace=env.get("AGENT_WORKSPACE", d.workspace), enable_file_tools=_bool(env.get("AGENT_ENABLE_FILE_TOOLS"), d.enable_file_tools),
            max_concurrent_runs=int(env.get("AGENT_MAX_CONCURRENT_RUNS", d.max_concurrent_runs)),
            max_steps_cap=int(env.get("AGENT_MAX_STEPS_CAP", d.max_steps_cap)),
            max_objective_chars=int(env.get("AGENT_MAX_OBJECTIVE_CHARS", d.max_objective_chars)),
            approval_timeout_s=float(env.get("AGENT_APPROVAL_TIMEOUT_S", d.approval_timeout_s)), log_level=env.get("LOG_LEVEL", d.log_level))

    def validate(self) -> None:
        if self.provider not in ("rule", "openai_compat"):
            raise ValueError(f"AGENT_PROVIDER must be 'rule' or 'openai_compat', got {self.provider!r}")
        if self.provider == "openai_compat" and not self.base_url:
            raise ValueError("AGENT_BASE_URL is required for openai_compat")
        if self.max_concurrent_runs < 1 or self.max_steps_cap < 1:
            raise ValueError("limits must be >= 1")

    def public(self) -> dict:
        """Safe-to-expose view (no secrets) for /readyz and logs."""
        return {"provider": self.provider, "model": self.model if self.provider != "rule" else None, "store": "postgres" if self.database_url else "memory",
                "auth": bool(self.api_token), "file_tools": self.enable_file_tools, "max_concurrent_runs": self.max_concurrent_runs}
