import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from threading import RLock
from typing import Any
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status

from src.core.config import settings


def is_trusted_proxy(request: Request) -> bool:
    return bool(request.client and request.client.host in settings.TRUSTED_PROXIES)


def is_https_request(request: Request) -> bool:
    return request.url.scheme == "https" or (
        is_trusted_proxy(request) and request.headers.get("x-forwarded-proto") == "https"
    )


def verify_request_origin(request: Request) -> None:
    """Compare against the public host only when the immediate peer is trusted."""
    host = request.headers.get("host", "")
    if is_trusted_proxy(request):
        host = request.headers.get("x-forwarded-host", host)
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    source, label = (origin, "Origin") if origin else (referer, "Referer")
    if not source:
        return
    try:
        parsed = urlparse(source)
        matches = (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
            and parsed.netloc.casefold() == host.casefold()
            and "," not in host
        )
    except ValueError:
        matches = False
    if not matches:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cross-origin request rejected ({label} mismatch)",
        )


def get_client_ip(request: Request) -> str:
    """Extract verified client IP by traversing the X-Forwarded-For chain backwards from trusted proxies."""
    client_host = request.client.host if request.client else "127.0.0.1"
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1"]))

    if client_host in trusted_proxies:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            # Proxies append the connecting IP to the end of XFF.
            # Traverse from right to left, skipping trusted proxies to find the real client IP.
            hops = [ip.strip() for ip in xff.split(",") if ip.strip()]
            for ip in reversed(hops):
                if ip not in trusted_proxies:
                    try:
                        import ipaddress

                        ipaddress.ip_address(ip)
                        return ip
                    except ValueError:
                        continue
            if hops:
                try:
                    import ipaddress

                    ipaddress.ip_address(hops[0])
                    return hops[0]
                except ValueError:
                    pass
    return client_host


def validate_safe_return_url(url: str | None) -> str:
    r"""Validate and sanitize redirect URL against open-redirect, javascript:, and protocol attacks.

    Strict allowlist: only local paths starting with /real/editor, /editor, or /real/.
    Rejects schemes (colon before query), protocol-relative (//), backslashes (\),
    control characters, and encoded exploits.
    Fallback is strictly /real/editor.
    """
    if not url or not isinstance(url, str):
        return "/real/editor"

    raw = url.strip()
    if not raw:
        return "/real/editor"

    # Reject null bytes and control chars
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):
        return "/real/editor"

    # Reject backslashes and encoded backslashes
    if "\\" in raw or "%5c" in raw.lower():
        return "/real/editor"

    # Reject protocol-relative '//' and encoded '%2f%2f'
    if raw.startswith("//") or "%2f%2f" in raw.lower():
        return "/real/editor"

    # Reject any colon before query string to block javascript:, data:, http:, etc.
    path_part = raw.split("?")[0]
    if ":" in path_part or "%3a" in path_part.lower():
        return "/real/editor"

    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(raw)
        if parsed.scheme or parsed.netloc:
            return "/real/editor"

        clean_path = parsed.path
        allowed_prefixes = ("/real/editor", "/editor", "/real/")
        if not any(clean_path == p or clean_path.startswith(p) for p in allowed_prefixes):
            return "/real/editor"

        result = clean_path
        if parsed.query:
            result = f"{result}?{parsed.query}"
        return result
    except Exception:
        return "/real/editor"


def generate_session_token() -> str:
    """Generate cryptographically secure 256-bit random session token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Compute SHA-256 digest of session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_key_fingerprint(key: str | None) -> str:
    """Compute SHA-256 fingerprint of API key."""
    if not key:
        return ""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_csrf_token(token_hash: str) -> str:
    """Generate CSRF token tied to the session token hash via HMAC-SHA256."""
    secret = settings.APP_SECRET_KEY.encode("utf-8")
    return hmac.new(secret, token_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_csrf_token(csrf_token: str, token_hash: str) -> bool:
    """Validate CSRF token constant-time against session token hash."""
    if not csrf_token or not token_hash:
        return False
    expected = generate_csrf_token(token_hash)
    return hmac.compare_digest(csrf_token.strip(), expected.strip())


class SecurityStore:
    """Persistent thread-safe SQLite security store for sessions and brute-force rate limits."""

    def __init__(self, db_url: str | None = None):
        self.db_url = (
            db_url
            or getattr(
                settings, "get_security_database_url", lambda: "sqlite:///./data/security.db"
            )()
        )
        self._db_path = self.db_url.replace("sqlite:///", "")
        self._credentials: dict[str, tuple[str, str]] = {}
        self._credentials_lock = RLock()
        if self._db_path and self._db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_sessions (
                    token_hash TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    client_ip TEXT NOT NULL,
                    key_fingerprint TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    client_ip TEXT PRIMARY KEY,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    tier INTEGER NOT NULL DEFAULT 0,
                    blocked_until REAL NOT NULL DEFAULT 0,
                    last_attempt_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    def check_rate_limit(self, client_ip: str, now: float | None = None) -> tuple[bool, int]:
        """Check if client IP is currently blocked.

        Returns (is_allowed, retry_after_seconds).
        """
        now = now if now is not None else time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT failed_attempts, tier, blocked_until, last_attempt_at FROM rate_limits WHERE client_ip = ?",
                (client_ip,),
            )
            row = cur.fetchone()
            if not row:
                return True, 0

            # If 24 hours elapsed since last attempt, reset bucket
            if now - row["last_attempt_at"] > 86400:
                conn.execute(
                    "UPDATE rate_limits SET failed_attempts = 0, tier = 0, blocked_until = 0 WHERE client_ip = ?",
                    (client_ip,),
                )
                conn.commit()
                return True, 0

            blocked_until = row["blocked_until"]
            if blocked_until > now:
                retry_after = int(blocked_until - now + 0.999)
                return False, retry_after

            return True, 0

    def record_failed_attempt(self, client_ip: str, now: float | None = None) -> tuple[int, int]:
        """Record failed login attempt and calculate backoff tier and block time.

        Returns (new_tier, retry_after_seconds).
        Tier 1: 5 attempts -> 2 min (120s)
        Tier 2: next 5 attempts -> 4 min (240s)
        Tier 3+: every next 5 attempts -> 16 min (960s max)
        """
        now = now if now is not None else time.time()
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "SELECT failed_attempts, tier, blocked_until, last_attempt_at FROM rate_limits WHERE client_ip = ?",
                (client_ip,),
            )
            row = cur.fetchone()

            if not row:
                failed = 1
                tier = 0
                blocked_until = 0.0
            else:
                # If 24 hours elapsed, reset bucket
                if now - row["last_attempt_at"] > 86400:
                    failed = 1
                    tier = 0
                    blocked_until = 0.0
                elif row["blocked_until"] > now:
                    # Client IP is CURRENTLY ALREADY BLOCKED!
                    # Do NOT increment failed_attempts, do NOT elevate tier, do NOT extend block!
                    remaining = int(row["blocked_until"] - now + 0.999)
                    conn.commit()
                    return row["tier"], remaining
                else:
                    failed = row["failed_attempts"] + 1
                    tier = row["tier"]
                    blocked_until = 0.0

            retry_after = 0
            # Every 5 failed attempts triggers the next tier block
            if failed > 0 and failed % 5 == 0:
                if tier == 0:
                    block_sec = 120
                    tier = 1
                elif tier == 1:
                    block_sec = 240
                    tier = 2
                else:
                    block_sec = 960  # Upper bound 16 minutes
                    tier = tier + 1
                blocked_until = now + block_sec
                retry_after = block_sec

            conn.execute(
                """
                INSERT INTO rate_limits (client_ip, failed_attempts, tier, blocked_until, last_attempt_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    failed_attempts = excluded.failed_attempts,
                    tier = excluded.tier,
                    blocked_until = excluded.blocked_until,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (client_ip, failed, tier, blocked_until, now),
            )
            conn.commit()
            return tier, retry_after

    def record_successful_login(
        self,
        client_ip: str,
        token_hash: str,
        key_fingerprint: str,
        now: float | None = None,
        *,
        api_key: str | None = None,
        tracker_url: str | None = None,
    ) -> dict[str, Any]:
        """Reset rate limit bucket and store authenticated session, checking for concurrent block race."""
        now = now if now is not None else time.time()
        idle_timeout = getattr(settings, "SESSION_IDLE_MINUTES", 30) * 60
        max_lifetime = getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 8) * 3600
        expires_at = min(now + max_lifetime, now + idle_timeout)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Verify IP is not blocked due to concurrent race
            cur = conn.execute(
                "SELECT blocked_until FROM rate_limits WHERE client_ip = ?",
                (client_ip,),
            )
            row = cur.fetchone()
            if row and row["blocked_until"] > now:
                remaining = int(row["blocked_until"] - now + 0.999)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Слишком много попыток. Повторите через {remaining} сек.",
                    headers={"Retry-After": str(remaining), "Cache-Control": "no-store"},
                )

            # Reset rate limits for this IP on successful login
            conn.execute(
                """
                INSERT INTO rate_limits (client_ip, failed_attempts, tier, blocked_until, last_attempt_at)
                VALUES (?, 0, 0, 0, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    failed_attempts = 0,
                    tier = 0,
                    blocked_until = 0,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (client_ip, now),
            )
            # Create session
            conn.execute(
                """
                INSERT INTO security_sessions (token_hash, created_at, last_seen_at, expires_at, client_ip, key_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token_hash, now, now, expires_at, client_ip, key_fingerprint),
            )
            conn.commit()

        if api_key and tracker_url:
            with self._credentials_lock:
                self._credentials[token_hash] = (api_key, tracker_url)
        self.cleanup_expired(now)
        return {
            "token_hash": token_hash,
            "created_at": now,
            "last_seen_at": now,
            "expires_at": expires_at,
            "client_ip": client_ip,
            "key_fingerprint": key_fingerprint,
        }

    def create_session(self, client_ip: str = "127.0.0.1", key: str | None = None) -> str:
        """Helper to create and return a raw session token (e.g. for testing or auth service)."""
        token = generate_session_token()
        token_h = hash_token(token)
        current_key = key or settings.get_real_keitaro_api_key()
        fp = get_key_fingerprint(current_key)
        from src.core.config import normalize_tracker_url

        self.record_successful_login(
            client_ip=client_ip,
            token_hash=token_h,
            key_fingerprint=fp,
            api_key=current_key,
            tracker_url=normalize_tracker_url(settings.get_real_keitaro_base_url()),
        )
        return token

    def get_authenticated_session(self, token_hash: str) -> dict[str, Any] | None:
        """Credentials live only in this worker's memory; restart requires a fresh login."""
        from src.core.config import normalize_tracker_url

        with self._credentials_lock:
            credential = self._credentials.get(token_hash)
        if not credential:
            self.revoke_session(token_hash)
            return None
        api_key, tracker_url = credential
        if tracker_url != normalize_tracker_url(settings.get_real_keitaro_base_url()):
            self.revoke_session(token_hash)
            return None
        session = self.get_valid_session(token_hash, get_key_fingerprint(api_key))
        if session is None:
            self.revoke_session(token_hash)
            return None
        session["_api_key"] = api_key
        session["_credential_scope"] = hmac.new(
            settings.APP_SECRET_KEY.encode(), f"{tracker_url}\n{api_key}".encode(), hashlib.sha256
        ).hexdigest()
        return session

    def get_valid_session(
        self,
        token_hash: str,
        current_key_fingerprint: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Fetch active session, verifying expiry and key fingerprint rotation."""
        now = now if now is not None else time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "SELECT token_hash, created_at, last_seen_at, expires_at, client_ip, key_fingerprint FROM security_sessions WHERE token_hash = ?",
                (token_hash,),
            )
            row = cur.fetchone()
            if not row:
                return None

            max_lifetime = getattr(settings, "SESSION_MAX_LIFETIME_HOURS", 8) * 3600
            idle_timeout = getattr(settings, "SESSION_IDLE_MINUTES", 30) * 60

            # Verify key fingerprint matches current server key
            if row["key_fingerprint"] != current_key_fingerprint:
                conn.execute("DELETE FROM security_sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
                return None

            # Check absolute lifetime and idle timeout
            if now > row["expires_at"] or now > (row["created_at"] + max_lifetime):
                conn.execute("DELETE FROM security_sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
                return None

            # Refresh last_seen and sliding idle expiry
            new_expires = min(row["created_at"] + max_lifetime, now + idle_timeout)
            conn.execute(
                "UPDATE security_sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                (now, new_expires, token_hash),
            )
            conn.commit()

            return {
                "token_hash": row["token_hash"],
                "created_at": row["created_at"],
                "last_seen_at": now,
                "expires_at": new_expires,
                "client_ip": row["client_ip"],
                "key_fingerprint": row["key_fingerprint"],
            }

    def revoke_session(self, token_hash: str) -> None:
        """Revoke a session by token hash."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM security_sessions WHERE token_hash = ?", (token_hash,))
            conn.commit()
        with self._credentials_lock:
            self._credentials.pop(token_hash, None)

    def cleanup_expired(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._credentials_lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM security_sessions WHERE expires_at < ?", (now,))
                conn.commit()
                active = {
                    row[0] for row in conn.execute("SELECT token_hash FROM security_sessions")
                }
            for token_hash in self._credentials.keys() - active:
                self._credentials.pop(token_hash, None)


_security_store: SecurityStore | None = None


def get_security_store() -> SecurityStore:
    global _security_store
    if _security_store is None:
        _security_store = SecurityStore()
    return _security_store


SESSION_COOKIE_NAME = "adrobot_session"


def get_current_session(
    request: Request,
    store: SecurityStore = Depends(get_security_store),
) -> dict[str, Any] | None:
    """Extract session token from cookie, validate, and return session dictionary."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    token_h = hash_token(token)
    return store.get_authenticated_session(token_h)


def require_real_session(
    request: Request,
    session: dict[str, Any] | None = Depends(get_current_session),
) -> dict[str, Any]:
    """Dependency enforcing that the request holds a valid real-mode session.

    Returns 401 JSON for API requests or redirects to /login for browser requests.
    """
    if session:
        return session

    is_api = request.url.path.startswith("/api/") or "application/json" in request.headers.get(
        "accept", ""
    )
    if is_api:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for real Keitaro mode",
        )

    # Browser UI redirect with safe return_to validation
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    safe_return = validate_safe_return_url(path)
    raise HTTPException(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Location": f"/login?return_to={safe_return}"},
    )


def require_csrf(
    request: Request,
    session: dict[str, Any] = Depends(require_real_session),
) -> None:
    """Validate CSRF token and origin for state-changing HTTP methods on real session."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    verify_request_origin(request)

    csrf_token = request.headers.get("x-csrf-token")
    if not csrf_token or not verify_csrf_token(csrf_token, session["token_hash"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token",
        )
