"""Encryption of stored API keys."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


class KeyBox:
    """Encrypts secrets with a Fernet key from ENCRYPTION_KEY or from data/secret.key."""

    def __init__(self, data_dir: Path, env_key: str | None):
        if env_key:
            key = env_key.encode()
        else:
            path = data_dir / "secret.key"
            if path.exists():
                key = path.read_bytes().strip()
            else:
                data_dir.mkdir(parents=True, exist_ok=True)
                key = Fernet.generate_key()
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(key)
                log.warning("Generated a new encryption key at %s. Back it up, without it saved API keys are lost.", path)
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            log.error("Could not decrypt a stored API key (was the encryption key changed?).")
            return None
