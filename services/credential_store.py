"""Nexus v2.0 — Fernet-based credential store for secrets at rest."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet

_fernet: Fernet | None = None
_KEY_FILE = ".nexus_fernet.key"


def init_credential_store(app_dir: Path) -> None:
    global _fernet
    key_path = app_dir / _KEY_FILE
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key + b"\n")
        os.chmod(str(key_path), 0o600)
    _fernet = Fernet(key)


def encrypt(plaintext: str) -> str:
    if _fernet is None:
        raise RuntimeError("Credential store not initialised")
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if _fernet is None:
        raise RuntimeError("Credential store not initialised")
    return _fernet.decrypt(ciphertext.encode()).decode()
