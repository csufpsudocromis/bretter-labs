import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

_SECRET_PREFIX = "enc:v1:"


def _derive_fernet_key(raw: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def _resolve_fernet() -> Fernet | None:
    configured = str(getattr(settings, "secrets_encryption_key", "") or "").strip()
    if not configured:
        return None
    key_material = configured.encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(key_material)
        if len(decoded) != 32:
            raise ValueError
        return Fernet(key_material)
    except Exception:
        return Fernet(_derive_fernet_key(configured))


def secret_is_encrypted(value: str | None) -> bool:
    return str(value or "").startswith(_SECRET_PREFIX)


def secret_is_configured(value: str | None) -> bool:
    return bool(str(value or "").strip())


def encrypt_secret(value: str | None) -> str:
    cleartext = str(value or "")
    if not cleartext:
        return ""
    if secret_is_encrypted(cleartext):
        return cleartext
    fernet = _resolve_fernet()
    if not fernet:
        return cleartext
    ciphertext = fernet.encrypt(cleartext.encode("utf-8")).decode("ascii")
    return f"{_SECRET_PREFIX}{ciphertext}"


def decrypt_secret(value: str | None) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if not secret_is_encrypted(raw):
        return raw
    fernet = _resolve_fernet()
    if not fernet:
        raise RuntimeError("Encrypted secret is configured but BLABS_SECRETS_ENCRYPTION_KEY is missing.")
    token = raw[len(_SECRET_PREFIX) :]
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Unable to decrypt secret with BLABS_SECRETS_ENCRYPTION_KEY.") from exc
