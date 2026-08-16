"""Configuration. Every value comes from the environment; nothing is hardcoded.

Credential rule carried from Pimlico: a credential that is absent or still
literally ``CHANGE_ME`` makes its connector **dormant**. It never produces a
401 loop and it never produces a fabricated result.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote

PLACEHOLDER = "CHANGE_ME"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _secret(name: str, default: str = "") -> str:
    """Read a value, preferring a ``*_FILE`` pointing at a Swarm secret.

    Swarm mounts secrets on tmpfs at /run/secrets. Reading from the file
    rather than the environment keeps the value out of ``docker inspect``,
    out of ``/proc/<pid>/environ``, and out of any crash dump that prints the
    environment — all three of which have leaked credentials on this host.
    """
    path = os.environ.get(f"{name}_FILE", "").strip()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            # Deliberately not fatal: an unreadable secret must make the
            # dependent connector DORMANT, not crash-loop the container.
            return ""
    return os.environ.get(name, default)


def _build_dsn() -> str:
    """Assemble the postgres DSN from parts, or take an explicit override.

    An explicit ``JPD_PG_DSN`` always wins — the test suite needs to point at
    a different database without reconstructing every part.

    ⚠️ 127.0.0.1, never ``localhost``: on this host ``localhost`` resolves to
    ``::1`` first and postgres listens on IPv4 only, which produces a
    connection refused that looks like the database is down.
    """
    explicit = os.environ.get("JPD_PG_DSN", "").strip()
    if explicit:
        return explicit
    user = _env("JPD_PG_USER", "jarvis")
    pw = _secret("JPD_PG_PASSWORD", "jarvis")
    host = _env("JPD_PG_HOST", "127.0.0.1")
    port = _env("JPD_PG_PORT", "5632")
    name = _env("JPD_PG_DB", "jarvis")
    return f"postgresql://{user}:{quote(pw, safe='')}@{host}:{port}/{name}"


def credential_present(name: str) -> bool:
    """True only if the variable is set to something that is not a placeholder.

    Returns a BOOLEAN. It does not, and must not, return the value — this
    function is called from status endpoints that are reachable publicly.
    """
    v = os.environ.get(name, "").strip()
    return bool(v) and v != PLACEHOLDER


@dataclass(frozen=True)
class Settings:
    # --- identity -----------------------------------------------------------
    service: str = field(default_factory=lambda: _env("JPD_SERVICE", "jarvis-core"))
    version: str = field(default_factory=lambda: _env("JPD_VERSION", "0.1.0"))
    env: str = field(default_factory=lambda: _env("JPD_ENV", "production"))

    # --- storage ------------------------------------------------------------
    pg_dsn: str = field(default_factory=_build_dsn)
    redis_url: str = field(default_factory=lambda: _env(
        "JPD_REDIS_URL", "redis://127.0.0.1:6581/0"))

    # --- runtime ------------------------------------------------------------
    lease_ttl_s: int = field(default_factory=lambda: int(_env("JPD_LEASE_TTL_S", "120")))
    default_timeout_s: int = field(default_factory=lambda: int(_env("JPD_STEP_TIMEOUT_S", "300")))
    repair_ceiling: int = field(default_factory=lambda: int(_env("JPD_REPAIR_CEILING", "3")))

    # --- paths --------------------------------------------------------------
    # Test paths declared on @step are resolved against this root, and the
    # registry REFUSES to register a step whose test file does not exist.
    package_root: str = field(default_factory=lambda: _env(
        "JPD_PACKAGE_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    artifact_dir: str = field(default_factory=lambda: _env("JPD_ARTIFACT_DIR", "/app/data/artifacts"))

    # --- egress -------------------------------------------------------------
    http_timeout_s: int = field(default_factory=lambda: int(_env("JPD_HTTP_TIMEOUT_S", "30")))


settings = Settings()


def credential_status() -> dict[str, bool]:
    """Booleans only. Deliberately never returns a value, not even a prefix."""
    names = [
        "JPD_TELEGRAM_BOT_TOKEN", "JPD_GHL_API_KEY", "JPD_STRIPE_SECRET",
        "JPD_YOUTUBE_API_KEY", "JPD_TUBEONAI_KEY", "JPD_YOUCOM_KEY",
        "JPD_DATABAR_KEY", "JPD_MAILGUN_KEY", "JPD_ANTHROPIC_API_KEY",
        "JPD_OPENROUTER_KEY", "JPD_REDDIT_CLIENT_ID", "JPD_REDDIT_CLIENT_SECRET",
    ]
    return {n: credential_present(n) for n in names}
