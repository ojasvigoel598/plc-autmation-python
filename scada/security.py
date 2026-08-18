"""Server-side authentication, sessions, authorization and abuse controls."""
from __future__ import annotations
import base64, hashlib, hmac, os, secrets, time
from collections import defaultdict, deque
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    user: str; password_hash: str; session_secret: str; admin_password_hash: str
    production: bool; cookie_secure: bool; max_attempts: int = 5; window_seconds: int = 300

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

def password_digest(password: str, salt: str | None = None) -> str:
    if not isinstance(password, str) or not password or len(password) > 256: raise ValueError("invalid password")
    salt = salt or secrets.token_urlsafe(16)
    raw = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt}${base64.urlsafe_b64encode(raw).decode()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split("$", 2)
        raw = hashlib.scrypt(password.encode(), salt=salt.encode(), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(base64.urlsafe_b64encode(raw).decode(), expected)
    except (ValueError, TypeError): return False

def load_settings() -> Settings:
    production = _env_bool("SCADA_PRODUCTION", False)
    user = os.getenv("SCADA_OPERATOR_USER", "operator")
    password_hash = os.getenv("SCADA_OPERATOR_PASSWORD_HASH", "")
    admin_hash = os.getenv("SCADA_ADMIN_PASSWORD_HASH", "")
    secret = os.getenv("SCADA_SESSION_SECRET", "")
    if production and (not password_hash or not admin_hash or len(secret) < 32): raise RuntimeError("SCADA authentication is not configured for production")
    if not password_hash: password_hash = password_digest(os.getenv("SCADA_DEV_PASSWORD", "change-me"), "local-dev")
    if not secret: secret = secrets.token_urlsafe(32)
    return Settings(user, password_hash, secret, admin_hash, production, _env_bool("SCADA_COOKIE_SECURE", production))

def make_session(secret: str, username: str, role: str, ttl: int = 3600) -> str:
    payload = f"{username}|{role}|{int(time.time()) + ttl}|{secrets.token_urlsafe(16)}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode().rstrip("=")

def read_session(secret: str, token: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        username, role, expiry, nonce, sig = raw.split("|", 4)
        unsigned = f"{username}|{role}|{expiry}|{nonce}"
        expected = hmac.new(secret.encode(), unsigned.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected) or int(expiry) < int(time.time()): return None
        if role not in {"operator", "admin"} or not username or len(username) > 128: return None
        return username, role
    except (ValueError, TypeError, UnicodeDecodeError): return None

class LoginLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts, self.window_seconds = max_attempts, window_seconds; self._attempts: dict[str, deque[float]] = defaultdict(deque)
    def allowed(self, key: str) -> bool:
        now = time.monotonic(); q = self._attempts[key]
        while q and now - q[0] > self.window_seconds: q.popleft()
        return len(q) < self.max_attempts
    def failed(self, key: str) -> None: self._attempts[key].append(time.monotonic())
    def success(self, key: str) -> None: self._attempts.pop(key, None)
