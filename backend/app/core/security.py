import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("hextrade.security")


class SecretRedactionFilter(logging.Filter):
    _markers = ("api_secret", "access_token", "authorization", "checksum", "app_master_key", "pwd=")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage().lower()
        except Exception:
            return True
        if any(marker in message for marker in self._markers):
            record.msg = "redacted log line"
            record.args = ()
        return True


def install_log_filter() -> None:
    filt = SecretRedactionFilter()
    logging.getLogger().addFilter(filt)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "hextrade"):
        logging.getLogger(name).addFilter(filt)


def fernet_for(master_key: str) -> Fernet:
    if not master_key or master_key.startswith("replace-with"):
        raise ValueError("APP_MASTER_KEY is not configured")
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(master_key: str, token: str) -> bytes:
    return fernet_for(master_key).encrypt(token.encode("utf-8"))


def decrypt_token(master_key: str, payload: bytes) -> str:
    try:
        return fernet_for(master_key).decrypt(payload).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("stored session could not be decrypted") from exc


def mask_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"
