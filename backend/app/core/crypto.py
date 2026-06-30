"""Encryption utilities for API Key protection."""
import os
from typing import Optional
from cryptography.fernet import Fernet


def _get_or_create_key() -> bytes:
    """Get or create the encryption key.
    
    Reads from environment variable NOVEL_AGENT_KEY.
    If not set, generates a new key and stores it in a local file.
    The key is a 32-byte url-safe base64-encoded bytes string.
    """
    env_key = os.environ.get("SIMING_KEY") or os.environ.get("MOSHU_KEY") or os.environ.get("NOVEL_AGENT_KEY")
    if env_key:
        return env_key.encode()

    key_file = os.environ.get("SIMING_KEY_FILE") or os.environ.get("MOSHU_KEY_FILE") or os.environ.get("NOVEL_AGENT_KEY_FILE")
    if not key_file:
        app_home = os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
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


_fernet: Optional[Fernet] = None


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
