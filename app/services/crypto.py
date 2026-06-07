import base64
from cryptography.fernet import Fernet
from app.config import settings


def _fernet() -> Fernet:
    key = settings.secret_key.encode()
    padded = key[:32].ljust(32, b"0")
    b64 = base64.urlsafe_b64encode(padded)
    return Fernet(b64)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
