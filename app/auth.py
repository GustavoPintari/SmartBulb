"""Users with scrypt-hashed passwords, login sessions and API tokens.

Everything lives in one JSON document; session and token secrets are stored
only as SHA-256 digests.
"""
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone

ROLES = ("admin", "user", "guest")
ROLE_RANK = {"guest": 0, "user": 1, "admin": 2}
USERNAME_RE = re.compile(r"^[a-z0-9_.-]{2,32}$")
MIN_PASSWORD = 8
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def _digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Accounts:
    def __init__(self, store):
        self._store = store
        for key in ("users", "sessions", "tokens"):
            store.data.setdefault(key, {})
        self._dummy_hash = hash_password(secrets.token_hex(8))

    @property
    def _data(self):
        return self._store.data

    def bootstrap(self, username, password):
        """Create the first admin from APP_USER/APP_PASSWORD when no user exists yet."""
        if not self._data["users"] and password:
            self._data["users"][username.lower()] = {
                "hash": hash_password(password), "role": "admin", "created": _now_iso()}
            self._store.save()

    def has_users(self):
        return bool(self._data["users"])

    def _public(self, name):
        return {"name": name, "role": self._data["users"][name]["role"]}

    def list_users(self):
        with self._store.lock:
            return [{"name": n, "role": u["role"], "created": u.get("created")}
                    for n, u in sorted(self._data["users"].items())]

    def authenticate(self, name, password):
        user = self._data["users"].get(name.strip().lower())
        # hash something even for unknown users so timing doesn't reveal them
        ok = check_password(password, user["hash"] if user else self._dummy_hash)
        return self._public(name.strip().lower()) if user and ok else None

    def create(self, name, password, role):
        name = name.strip().lower()
        if not USERNAME_RE.match(name):
            raise ValueError("usuário deve ter 2-32 caracteres: letras minúsculas, números, . _ -")
        self._check_password(password)
        if role not in ROLES:
            raise ValueError(f"papel inválido: {role}")
        with self._store.lock:
            if name in self._data["users"]:
                raise ValueError("usuário já existe")
            self._data["users"][name] = {
                "hash": hash_password(password), "role": role, "created": _now_iso()}
            self._store.save()
        return self._public(name)

    def update(self, name, password=None, role=None):
        with self._store.lock:
            user = self._get(name)
            if role is not None:
                if role not in ROLES:
                    raise ValueError(f"papel inválido: {role}")
                if user["role"] == "admin" and role != "admin":
                    self._ensure_other_admin(name)
                user["role"] = role
            if password is not None:
                self._check_password(password)
                user["hash"] = hash_password(password)
            self._store.save()
        return self._public(name)

    def prefs(self, name):
        user = self._data["users"].get(name) or {}
        return dict(user.get("prefs") or {})

    def set_prefs(self, name, prefs):
        with self._store.lock:
            self._get(name)["prefs"] = prefs
            self._store.save()

    def change_password(self, name, current, new):
        if not self.authenticate(name, current):
            raise ValueError("senha atual incorreta")
        self.update(name, password=new)

    def delete(self, name):
        with self._store.lock:
            user = self._get(name)
            if user["role"] == "admin":
                self._ensure_other_admin(name)
            del self._data["users"][name]
            self.end_sessions(name)
            self._data["tokens"] = {k: t for k, t in self._data["tokens"].items() if t["user"] != name}
            self._store.save()

    def _get(self, name):
        user = self._data["users"].get(name)
        if not user:
            raise LookupError("usuário não encontrado")
        return user

    def _ensure_other_admin(self, name):
        if not any(u["role"] == "admin" for n, u in self._data["users"].items() if n != name):
            raise ValueError("é preciso manter pelo menos um administrador")

    @staticmethod
    def _check_password(password):
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"a senha precisa ter pelo menos {MIN_PASSWORD} caracteres")

    # --- sessions (browser login)

    def new_session(self, name, days):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._store.lock:
            sessions = self._data["sessions"]
            for key in [k for k, s in sessions.items() if s["expires"] < now]:
                del sessions[key]
            sessions[_digest(token)] = {"user": name, "expires": now + days * 86400}
            self._store.save()
        return token

    def session_user(self, token):
        session = self._data["sessions"].get(_digest(token))
        if not session or session["expires"] < time.time() or session["user"] not in self._data["users"]:
            return None
        return self._public(session["user"])

    def end_session(self, token):
        with self._store.lock:
            if self._data["sessions"].pop(_digest(token), None):
                self._store.save()

    def end_sessions(self, name, keep=None):
        """Log a user out everywhere, optionally keeping the session `keep`."""
        keep = keep and _digest(keep)
        with self._store.lock:
            sessions = self._data["sessions"]
            for key in [k for k, s in sessions.items() if s["user"] == name and k != keep]:
                del sessions[key]
            self._store.save()

    # --- API tokens (CLI, scripts, Home Assistant, phone shortcuts)

    def new_token(self, name, label):
        label = (label or "").strip()[:40] or "token"
        token = "sb_" + secrets.token_urlsafe(32)
        token_id = secrets.token_hex(4)
        with self._store.lock:
            self._get(name)
            self._data["tokens"][_digest(token)] = {
                "id": token_id, "user": name, "label": label, "created": _now_iso(), "last_used": None}
            self._store.save()
        return {"id": token_id, "label": label, "token": token}

    def tokens(self, name):
        return [{k: t[k] for k in ("id", "label", "created", "last_used")}
                for t in self._data["tokens"].values() if t["user"] == name]

    def revoke_token(self, name, token_id):
        with self._store.lock:
            key = next((k for k, t in self._data["tokens"].items()
                        if t["user"] == name and t["id"] == token_id), None)
            if not key:
                raise LookupError("token não encontrado")
            del self._data["tokens"][key]
            self._store.save()

    def token_user(self, token):
        entry = self._data["tokens"].get(_digest(token))
        if not entry or entry["user"] not in self._data["users"]:
            return None
        today = _now_iso()[:10]
        if (entry["last_used"] or "")[:10] != today:  # write at most once a day
            with self._store.lock:
                entry["last_used"] = _now_iso()
                self._store.save()
        return self._public(entry["user"])


class LoginThrottle:
    """Lock out an address for a while after repeated failed logins."""

    def __init__(self, attempts=5, lock_seconds=60):
        self._attempts = attempts
        self._lock_seconds = lock_seconds
        self._failures = {}

    def wait_time(self, key):
        count, until = self._failures.get(key, (0, 0))
        return max(0, int(until - time.time()))

    def failed(self, key):
        count, _ = self._failures.get(key, (0, 0))
        count += 1
        until = time.time() + self._lock_seconds if count >= self._attempts else 0
        self._failures[key] = (0 if until else count, until)

    def succeeded(self, key):
        self._failures.pop(key, None)
