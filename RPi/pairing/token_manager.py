"""
pairing/token_manager.py — per-pairing credential management.

Generates and persists:
  • A 32-byte random authentication token (raw bytes on disk, mode 0o600).
  • A self-signed EC P-256 TLS certificate + private key (PEM, mode 0o644/0o600).

GATT payload (64 bytes):  token[0:32] || SHA-256(cert DER)[32:64]

Both are overwritten atomically on every new pairing so only the most-recently-
paired phone holds valid credentials.
"""

import hashlib
import logging
import datetime
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

log = logging.getLogger(__name__)

_DATA_DIR   = Path("/var/lib/dawggles")
_TOKEN_FILE = _DATA_DIR / "pairing_token.bin"
_CERT_FILE  = _DATA_DIR / "server.crt"
_KEY_FILE   = _DATA_DIR / "server.key"


def generate_credentials() -> bytes:
    """Generate a fresh token + self-signed TLS cert, overwriting any previous ones.

    Returns the 64-byte GATT payload:
        bytes[0:32]  = authentication token
        bytes[32:64] = SHA-256 fingerprint of the TLS certificate (DER)
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Token ──────────────────────────────────────────────────────────────────
    token = secrets.token_bytes(32)
    _TOKEN_FILE.write_bytes(token)
    _TOKEN_FILE.chmod(0o600)

    # ── Self-signed EC P-256 TLS certificate ───────────────────────────────────
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dawggles")])
    now  = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    _CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _CERT_FILE.chmod(0o644)
    _KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    _KEY_FILE.chmod(0o600)

    # ── Fingerprint ────────────────────────────────────────────────────────────
    cert_der    = cert.public_bytes(serialization.Encoding.DER)
    fingerprint = hashlib.sha256(cert_der).digest()  # 32 bytes

    log.info(
        "token_manager: credentials generated (token=%.8s…, fp=%.8s…)",
        token.hex(), fingerprint.hex(),
    )
    return token + fingerprint  # 64 bytes


def load_token() -> bytes | None:
    """Return the stored 32-byte auth token, or None if missing/corrupt."""
    try:
        data = _TOKEN_FILE.read_bytes()
        if len(data) != 32:
            raise ValueError(f"unexpected length {len(data)}")
        return data
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("token_manager: load_token failed: %s", e)
        return None


def cert_path() -> str:
    return str(_CERT_FILE)


def key_path() -> str:
    return str(_KEY_FILE)


def has_credentials() -> bool:
    """True when all three credential files exist on disk."""
    return _TOKEN_FILE.exists() and _CERT_FILE.exists() and _KEY_FILE.exists()
