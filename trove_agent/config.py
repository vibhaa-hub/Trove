"""Runtime configuration from environment variables (12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    host: str
    port: int
    public_origin: str | None
    trust_proxy: bool
    notifier: str
    separate_reviewer: bool
    allow_signup: bool
    run_worker: bool
    worker_interval: int
    encoder: str
    log_json: bool
    data_region: str = ""
    enforce_residency: bool = False

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def secure_cookies(self) -> bool:
        return bool(self.public_origin and self.public_origin.startswith("https://"))


def load(database_url: str | None = None, host: str | None = None,
         port: int | None = None) -> Settings:
    env = os.getenv("TROVE_ENV", "development").strip().casefold()
    production = env == "production"
    origin = os.getenv("TROVE_PUBLIC_ORIGIN")
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
            raise ValueError("TROVE_PUBLIC_ORIGIN must look like https://trove.example.com")
        origin = f"{parsed.scheme}://{parsed.netloc}"
    settings = Settings(
        env=env,
        database_url=database_url or os.getenv("DATABASE_URL", "trove.db"),
        host=host or os.getenv("TROVE_HOST", "0.0.0.0" if production else "127.0.0.1"),
        port=port or int(os.getenv("TROVE_PORT", "8000")),
        public_origin=origin,
        trust_proxy=_bool("TROVE_TRUST_PROXY", production),
        notifier=os.getenv("TROVE_NOTIFIER", "live" if production else "dev"),
        separate_reviewer=_bool("TROVE_SEPARATE_REVIEWER", production),
        allow_signup=_bool("TROVE_ALLOW_SIGNUP", not production),
        run_worker=_bool("TROVE_RUN_WORKER", not production),
        worker_interval=int(os.getenv("TROVE_WORKER_INTERVAL", "60")),
        encoder=os.getenv("TROVE_ENCODER", "auto"),
        log_json=_bool("TROVE_LOG_JSON", production),
        # where this deployment stores data (in, uk, us, eu ...); see core/jurisdictions.py
        data_region=os.getenv("TROVE_DATA_REGION", "").strip().casefold(),
        enforce_residency=_bool("TROVE_ENFORCE_RESIDENCY", False),
    )
    if production:
        missing = [name for name in ("TROVE_VAULT_KEY", "TROVE_PEPPER", "TROVE_PUBLIC_ORIGIN")
                   if not os.getenv(name)]
        if missing:
            raise RuntimeError(
                "Production requires these environment variables: " + ", ".join(missing))
        if not settings.database_url.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("Production requires DATABASE_URL to point at PostgreSQL")
        if settings.notifier == "dev":
            raise RuntimeError("TROVE_NOTIFIER=dev shows one-time codes on screen; not allowed in production")
    if settings.enforce_residency and not settings.data_region:
        raise RuntimeError("TROVE_ENFORCE_RESIDENCY needs TROVE_DATA_REGION (e.g. in, uk, us, eu)")
    return settings


_current: Settings | None = None


def current() -> Settings:
    global _current
    if _current is None:
        _current = load()
    return _current


def set_current(settings: Settings) -> None:
    global _current
    _current = settings
