"""Security primitives for nominee/beneficiary verification.

Everything here is standard-library only so it can be audited easily:

* RFC 6238 TOTP (authenticator-app MFA) with replay protection
* one-time codes for email / phone possession checks (hashed, expiring,
  attempt-limited)
* keyed hashing of identifiers (national-ID last digits, DOB) so the database
  never stores them in clear text
* an in-memory sliding-window rate limiter
* a hash chain for tamper-evident audit events
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

TOTP_PERIOD = 30
TOTP_DIGITS = 6
OTP_TTL_SECONDS = 10 * 60
OTP_MAX_ATTEMPTS = 5
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 15 * 60


# --------------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _b32decode(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded, casefold=True)


def totp_at(secret: str, step: int) -> str:
    digest = hmac.new(_b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (
        10**TOTP_DIGITS
    )
    return f"{code:0{TOTP_DIGITS}d}"


def current_step(at: float | None = None) -> int:
    return int((time.time() if at is None else at) // TOTP_PERIOD)


def verify_totp(secret: str, code: Any, last_used_step: int | None) -> int | None:
    """Return the matched time step, or None. Rejects replays of used steps."""
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
        return None
    code = code.strip()
    now_step = current_step()
    for step in (now_step - 1, now_step, now_step + 1):
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(totp_at(secret, step), code):
            return step
    return None


def totp_uri(secret: str, account: str, issuer: str = "Trove Legacy Vault") -> str:
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
        f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


# --------------------------------------------------------- one-time codes
def new_numeric_code(digits: int = 6) -> str:
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def new_invite_code() -> str:
    # 4 groups of 4 from an unambiguous alphabet (~80 bits)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


class Pepper:
    """Server-side secret used to key hashes of identifiers and codes."""

    def __init__(self, root: Path) -> None:
        env = os.getenv("TROVE_PEPPER")
        if env:
            self.value = env.encode("utf-8")
            return
        path = root / ".pepper"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self.value = path.read_bytes()

    def keyed_hash(self, purpose: str, value: str) -> str:
        message = f"{purpose}:{value}".encode("utf-8")
        return hmac.new(self.value, message, hashlib.sha256).hexdigest()

    def matches(self, purpose: str, value: str, expected: str | None) -> bool:
        if not expected:
            return False
        return hmac.compare_digest(self.keyed_hash(purpose, value), expected)


def normalize_identifier(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("A valid email address is required")
    return email


def normalize_phone(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("phone must be a string")
    digits = re.sub(r"[^\d+]", "", value.strip())
    if not re.fullmatch(r"\+?\d{7,15}", digits):
        raise ValueError("A valid phone number (7-15 digits, optional +) is required")
    return digits


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str) -> str:
    return f"***{phone[-3:]}"


def password_strength_error(password: Any) -> str | None:
    if not isinstance(password, str) or len(password) < 12:
        return "Password must be at least 12 characters"
    if len(password) > 1024:
        return "Password is too long"
    classes = sum(
        bool(re.search(pattern, password))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
    )
    if classes < 3:
        return "Password must mix at least 3 of: lowercase, uppercase, digits, symbols"
    return None


# ------------------------------------------------------------ rate limit
class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record an attempt; return False when the limit is exceeded."""
        now_time = time.monotonic()
        with self._lock:
            events = self._events[key]
            while events and now_time - events[0] > window_seconds:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now_time)
            return True


# ----------------------------------------------------------- audit chain
GENESIS = "0" * 64


def chain_hash(previous_hash: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{previous_hash}|{canonical}".encode("utf-8")).hexdigest()
