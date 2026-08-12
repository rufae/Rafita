import base64
from pathlib import Path

from cryptography.fernet import Fernet

from src.config import settings
from src.logger import logger

ENV_PATH = Path(__file__).resolve().parent.parent.parent.parent / ".env"


def get_or_create_encryption_key() -> bytes:
    if settings.encryption_key_bytes:
        return settings.encryption_key_bytes
    key = Fernet.generate_key()
    key_b64 = base64.urlsafe_b64encode(key).decode("utf-8")
    try:
        if ENV_PATH.exists():
            content = ENV_PATH.read_text(encoding="utf-8")
            if "ENCRYPTION_KEY=" not in content:
                with open(str(ENV_PATH), "a", encoding="utf-8") as f:
                    f.write("\n# Clave maestra para cifrado AES-256 (generada automaticamente)\n")
                    f.write("ENCRYPTION_KEY=%s\n" % key_b64)
                logger.info("Encryption key generated and saved to .env")
            else:
                logger.warning("ENCRYPTION_KEY already in .env but empty; updating")
                new_lines = []
                for line in content.splitlines(keepends=True):
                    if line.strip().startswith("ENCRYPTION_KEY=") and (
                        'ENCRYPTION_KEY=""' in line or "ENCRYPTION_KEY=" in line.strip(" '\"")
                    ):
                        new_lines.append("ENCRYPTION_KEY=%s\n" % key_b64)
                    else:
                        new_lines.append(line)
                ENV_PATH.write_text("".join(new_lines), encoding="utf-8")
        else:
            with open(str(ENV_PATH), "w", encoding="utf-8") as f:
                f.write("ENCRYPTION_KEY=%s\n" % key_b64)
            logger.info("Encryption key generated and .env created")
    except Exception as e:
        logger.warning("Could not persist encryption key to .env: %s. Using in-memory key.", e)
    return key


_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        key = get_or_create_encryption_key()
        _cipher = Fernet(key)
    return _cipher


def encrypt_value(plain_text: str) -> str:
    if not plain_text:
        return plain_text
    try:
        cipher = _get_cipher()
        token = cipher.encrypt(plain_text.encode("utf-8"))
        return base64.urlsafe_b64encode(token).decode("utf-8")
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        return plain_text


def decrypt_value(cipher_text: str) -> str:
    if not cipher_text:
        return cipher_text
    try:
        raw = base64.urlsafe_b64decode(cipher_text.encode("utf-8"))
        cipher = _get_cipher()
        return cipher.decrypt(raw).decode("utf-8")
    except Exception as e:
        logger.debug("Decryption failed (may be plaintext): %s", e)
        return cipher_text
