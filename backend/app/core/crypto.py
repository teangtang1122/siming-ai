"""Encryption utilities for API Key protection."""
import os

from cryptography.fernet import Fernet

from .legacy_env import get_compatible_env


def _get_or_create_key() -> bytes:
    """Get or create the encryption key.
    
    Reads from the canonical SIMING_KEY environment variable.
    If not set, generates a new key and stores it in a local file.
    The key is a 32-byte url-safe base64-encoded bytes string.
    """
    env_key = get_compatible_env("SIMING_KEY")
    if env_key:
        return env_key.encode()

    key_file = get_compatible_env("SIMING_KEY_FILE")
    if not key_file:
        app_home = get_compatible_env("SIMING_HOME")
        if app_home:
            key_file = os.path.join(app_home, ".crypto_key")
        else:
            key_file = os.path.join(os.path.dirname(__file__), "..", "..", ".crypto_key")
    key_file = os.path.abspath(key_file)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return f.read()
    
    key = Fernet.generate_key()
    with open(key_file, "wb") as f:
        f.write(key)
    os.chmod(key_file, 0o600)
    return key


_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    """Get or create Fernet instance."""
    global _fernet
    if _fernet is None:
        key = _get_or_create_key()
        _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return base64-encoded ciphertext."""
    f = get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string."""
    f = get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
