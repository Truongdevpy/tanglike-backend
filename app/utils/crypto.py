import base64
import hashlib
from typing import Optional
from cryptography.fernet import Fernet
from app.config.settings import settings

def _get_fernet() -> Fernet:
    # SECRET_KEY is independent from JWT signing keys so access-token rotation
    # never makes encrypted provider credentials unreadable.
    secret_str = settings.SECRET_KEY or settings.JWT_SECRET
    key_material = hashlib.sha256(secret_str.encode('utf-8')).digest()
    fernet_key = base64.urlsafe_b64encode(key_material)
    return Fernet(fernet_key)

def encrypt_secret(plaintext: Optional[str]) -> str:
    if not plaintext:
        return ''
    f = _get_fernet()
    token = f.encrypt(plaintext.encode('utf-8')).decode('utf-8')
    return f'enc:{token}'

def decrypt_secret(ciphertext: Optional[str]) -> str:
    if not ciphertext:
        return ''
    if not ciphertext.startswith('enc:'):
        return ciphertext
    try:
        token = ciphertext[4:].encode('utf-8')
        f = _get_fernet()
        return f.decrypt(token).decode('utf-8')
    except Exception:
        return ''
