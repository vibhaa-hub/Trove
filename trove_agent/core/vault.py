"""Encrypted local document vault for the single-user prototype."""

from __future__ import annotations

import os
from threading import Lock
from pathlib import Path

from trove_agent.core.store import IntegrationError


class LocalVault:
    MAX_FILE_SIZE = 5 * 1024 * 1024

    def __init__(self, database_path: str | Path) -> None:
        is_url = "://" in str(database_path)
        db_path = Path(str(database_path))
        if os.getenv("TROVE_VAULT_DIR"):
            root = Path(os.environ["TROVE_VAULT_DIR"])
        elif is_url:
            root = Path.cwd() / "trove_vault"
        elif str(database_path) == ":memory:":
            root = Path.cwd() / ".trove-local"
        else:
            root = db_path.parent / f"{db_path.stem}_vault"
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.key_path = root / ".vault-key"
        self._key_lock = Lock()

    def _fernet(self):
        try:
            from cryptography.fernet import Fernet, InvalidToken
        except ImportError as exc:
            raise IntegrationError(
                "Vault encryption requires the cryptography dependency"
            ) from exc
        key = os.getenv("TROVE_VAULT_KEY")
        if key is None:
            with self._key_lock:
                if not self.key_path.exists():
                    self.key_path.write_bytes(Fernet.generate_key())
                    try:
                        os.chmod(self.key_path, 0o600)
                    except OSError:
                        pass
                key = self.key_path.read_text(encoding="ascii").strip()
        try:
            return Fernet(key.encode("ascii")), InvalidToken
        except (ValueError, UnicodeEncodeError) as exc:
            raise IntegrationError("TROVE_VAULT_KEY is not a valid Fernet key") from exc

    def store(self, document_id: str, data: bytes) -> str:
        if not data or len(data) > self.MAX_FILE_SIZE:
            raise ValueError("Document must be between 1 byte and 5 MB")
        fernet, _ = self._fernet()
        path = self.root / f"{document_id}.enc"
        encrypted = fernet.encrypt(data)
        with path.open("xb") as document_file:
            document_file.write(encrypted)
        return str(path)

    def read(self, stored_path: str) -> bytes:
        path = Path(stored_path).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".enc":
            raise ValueError("Document path is outside the configured vault")
        fernet, invalid_token = self._fernet()
        try:
            return fernet.decrypt(path.read_bytes())
        except invalid_token as exc:
            raise IntegrationError("Vault document could not be decrypted") from exc

    def delete(self, stored_path: str) -> None:
        path = Path(stored_path).resolve()
        if path.parent != self.root.resolve() or path.suffix != ".enc":
            raise ValueError("Document path is outside the configured vault")
        if path.exists():
            path.unlink()
