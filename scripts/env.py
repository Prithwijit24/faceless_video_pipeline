"""
Central environment loader.

Development:
    Put a .env file at the repository/project root:
        faceless-pipeline/.env

Production / CI:
    Inject variables through the process environment or your CI secret store.
    No .env file is required.

Resolution order:
1. ENV_FILE, when explicitly supplied.
2. <project-root>/.env.
3. A .env found while walking upward from the current working directory.

Existing process environment variables always win over .env values.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_env_file() -> Path | None:
    explicit = os.environ.get("ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        return p

    root_env = PROJECT_ROOT / ".env"
    if root_env.is_file():
        return root_env

    # Useful when the project is invoked from a nested working directory or
    # from a wrapper script. Never prefer this over the actual project root.
    current = Path.cwd().resolve()
    for parent in (current, *current.parents):
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


ENV_FILE_PATH = _resolve_env_file()
ENV_FILE_LOADED = bool(ENV_FILE_PATH and load_dotenv(ENV_FILE_PATH, override=False))


def env_source() -> str:
    """Return a non-secret description useful for diagnostics."""
    if ENV_FILE_LOADED and ENV_FILE_PATH:
        return str(ENV_FILE_PATH)
    return "process environment / CI secrets (no .env file loaded)"


# Substrings that mark a secret value as an unfilled template placeholder.
_PLACEHOLDER_HINTS = (
    "your-", "your_", "changeme", "placeholder", "xxxx", "example",
    "replace", "put your", "<",
)


def looks_unfilled(value):
    """True when a secret value is empty or looks like a template placeholder."""
    v = (value or "").strip()
    if not v:
        return True
    return any(h in v.lower() for h in _PLACEHOLDER_HINTS)
