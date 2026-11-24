"""Backups: database snapshot + encrypted vault files, in one timestamped folder.

* SQLite: online backup API (consistent copy while the app runs)
* PostgreSQL: ``pg_dump --format=custom`` (must be on PATH, e.g. in the
  postgres container or the app image)

Vault files are already Fernet-encrypted. The vault key and pepper are NOT
included; store them in your secret manager. A backup without the key cannot
be decrypted, which is intended. Test restores regularly.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from trove_agent.core.db import is_postgres
from trove_agent.core.vault import LocalVault


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_backup(database_url: str, out_dir: Path) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = out_dir / f"trove-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    if is_postgres(database_url):
        dump = target / "database.pgdump"
        subprocess.run(["pg_dump", "--format=custom", f"--file={dump}", database_url],
                       check=True, timeout=3600)
        files[dump.name] = _sha256(dump)
    else:
        dump = target / "database.sqlite"
        source = sqlite3.connect(database_url)
        destination = sqlite3.connect(dump)
        with destination:
            source.backup(destination)
        source.close()
        destination.close()
        files[dump.name] = _sha256(dump)
    vault = LocalVault(database_url).root
    vault_out = target / "vault"
    vault_out.mkdir()
    for path in vault.glob("*.enc"):
        shutil.copy2(path, vault_out / path.name)
        files[f"vault/{path.name}"] = _sha256(vault_out / path.name)
    manifest = {"created_at": stamp, "database": "postgres" if is_postgres(database_url) else "sqlite",
                "files": files, "note": "Vault key and pepper are not included; restore needs them."}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.chmod(target, 0o700)
    return {"backup": str(target), "files": len(files)}
