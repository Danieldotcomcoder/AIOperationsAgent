"""Local development signing key (ADR 0008).

Generates an RSA keypair on first use and reuses it afterwards, so tokens survive an
API restart. The private key is written outside the repository tree by default
(``.data/`` is git-ignored) and never leaves this machine.

Every function here refuses to run outside development/test. This module is the one
place a private signing key exists in the codebase, so the gate lives at the top of
each entry point rather than being assumed from the caller.
"""

from __future__ import annotations

from contextlib import suppress
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from opspilot.core.config import Settings
from opspilot.core.logging import get_logger

log = get_logger(__name__)

_KEY_SIZE = 2048


def _guard(settings: Settings) -> None:
    if not settings.app_env.is_local:
        raise RuntimeError(
            f"dev signing keys are refused with APP_ENV={settings.app_env}; use AUTH_MODE=auth0"
        )


def _key_path(settings: Settings) -> Path:
    path = Path(settings.dev_auth_signing_key_path)
    if not path.is_absolute():
        from opspilot.core.config import REPO_ROOT

        path = REPO_ROOT / path
    return path


@lru_cache(maxsize=1)
def _load_or_create(private_key_path: str, app_env: str) -> tuple[bytes, bytes]:
    """Return ``(private_pem, public_pem)``, generating the pair on first use.

    Cached on the path so repeated verification does not re-read and re-parse the key
    on every request.
    """
    path = Path(private_key_path)
    if path.exists():
        private_pem = path.read_bytes()
        private_key = serialization.load_pem_private_key(private_pem, password=None)
    else:
        log.info("dev_auth.generating_keypair", path=str(path), env=app_env)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(private_pem)
        # Best effort on POSIX; Windows ACLs are left to the filesystem.
        with suppress(OSError):
            path.chmod(0o600)

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def private_key_pem(settings: Settings) -> bytes:
    _guard(settings)
    return _load_or_create(str(_key_path(settings)), settings.app_env.value)[0]


def public_key_pem(settings: Settings) -> bytes:
    _guard(settings)
    return _load_or_create(str(_key_path(settings)), settings.app_env.value)[1]
