import hashlib
import hmac
import secrets

from app.models.game.room import RoomError


class RoomPasswordRequired(RoomError):
    pass


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)


def hash_room_password(password: str) -> str | None:
    if not isinstance(password, str) or len(password) > 64:
        raise RoomError("Пароль должен содержать от 4 до 64 символов")
    if password == "":
        return None
    if len(password) < 4 or not password.strip():
        raise RoomError("Пароль должен содержать от 4 до 64 символов")
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{_derive(password, salt).hex()}"


def check_room_password(stored: str | None, password: str | None) -> None:
    if not stored:
        return
    if password in (None, ""):
        raise RoomPasswordRequired("Для входа в лобби нужен пароль")
    if not isinstance(password, str) or len(password) > 64:
        raise RoomError("Неверный пароль")
    salt, digest = stored.split(":", 1)
    if not hmac.compare_digest(_derive(password, bytes.fromhex(salt)).hex(), digest):
        raise RoomError("Неверный пароль")
