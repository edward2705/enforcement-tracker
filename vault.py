"""Encryption at rest for the PCE credentials in config.yaml and IP addresses in tracker.db.

AES-256-GCM (authenticated encryption): a random 96-bit nonce per value, and the field name as
associated data, so a value copied into another field does not decrypt. Encrypted values are stored
as "enc:v1:<base64url(nonce + ciphertext + tag)>"; anything else is plain text, which is how values
written before encryption are read until they are rewritten.

The 256-bit key comes from the TRACKER_SECRET_KEY environment variable (base64url) or, when that is
not set, from secret.key next to this file, created on first use and readable only by the owner.
Keep the key out of git and out of backups of tracker.db and config.yaml: whoever has both can read
the values. Losing the key means re-entering the PCE credentials and running a sync."""
import base64
import ipaddress
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_PATH = os.environ.get("TRACKER_SECRET_KEY_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "secret.key")
PREFIX = "enc:v1:"
NONCE_BYTES = 12
_key = None


class VaultError(Exception):
    pass


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load_key():
    global _key
    if _key:
        return _key
    raw = os.environ.get("TRACKER_SECRET_KEY")
    if raw:
        key = _unb64(raw.strip())
    elif os.path.exists(KEY_PATH):
        with open(KEY_PATH) as fh:
            key = _unb64(fh.read().strip())
    else:
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(_b64(key) + "\n")
    if len(key) != 32:
        raise VaultError("the encryption key must be 32 bytes (AES-256)")
    _key = key
    return key


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value, field):
    """Encrypt a text value for `field`; empty and already encrypted values are returned as they are."""
    if value is None or value == "" or is_encrypted(value):
        return value
    nonce = secrets.token_bytes(NONCE_BYTES)
    sealed = AESGCM(_load_key()).encrypt(nonce, str(value).encode(), field.encode())
    return PREFIX + _b64(nonce + sealed)


def decrypt(value, field):
    """The plain text of a value from `field`; plain text (not yet encrypted) is returned as it is."""
    if not is_encrypted(value):
        return value
    try:
        blob = _unb64(value[len(PREFIX):])
        return AESGCM(_load_key()).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], field.encode()).decode()
    except (InvalidTag, ValueError) as exc:
        raise VaultError(f"cannot decrypt {field}: wrong key or damaged value") from exc


def is_ip(value):
    try:
        ipaddress.ip_address(str(value).strip())
        return True
    except ValueError:
        return False


def encrypt_if_ip(value, field):
    """IP addresses are encrypted; host names are kept readable so they can be searched and sorted."""
    return encrypt(value, field) if value and is_ip(value) else value


def reveal(value, field):
    """decrypt() for display: a value that cannot be decrypted shows as "(encrypted)" instead of failing."""
    try:
        return decrypt(value, field)
    except VaultError:
        return "(encrypted)"
