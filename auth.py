"""
auth.py — Flask-Login authentication module for Fitness Dashboard.
SQLite-backed, no SQLAlchemy. Minimal proof-of-concept for SaaS productization.
"""

import os
import fcntl
import sqlite3
import hmac
import hashlib
import ipaddress
import logging
import re
import secrets
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
from flask import Blueprint, current_app, jsonify, request, redirect, url_for, render_template, flash, session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from runtime_config import data_path
from werkzeug.security import check_password_hash, generate_password_hash

# ── Rate limiting (SQLite-backed, shared across workers) ──
_RATE_LIMIT_WINDOW_SEC = 600   # 10 minutes
_RATE_LIMIT_MAX_FAILS  = 10    # max failures before lockout
_rate_limit_hmac_key: bytes | None = None


def _load_or_create_secret(secret_file: str) -> str:
    """Read or initialize the fallback secret under a cross-process file lock."""
    try:
        fd = os.open(secret_file, os.O_RDONLY)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("Could not read the fallback SECRET_KEY") from exc
    else:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            secret = handle.read().strip()
            if secret:
                return secret

    try:
        fd = os.open(secret_file, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            secret = handle.read().strip()
            if not secret:
                secret = secrets.token_hex(64)
                handle.seek(0)
                handle.truncate()
                handle.write(secret)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(secret_file, 0o600)
            return secret
    except OSError as exc:
        raise RuntimeError(
            "Could not persist the fallback SECRET_KEY; set SECRET_KEY explicitly"
        ) from exc


def _rate_identity_hash(identity: str) -> str:
    if _rate_limit_hmac_key is None:
        raise RuntimeError("Rate-limit key is unavailable before auth initialization")
    return hmac.new(
        _rate_limit_hmac_key,
        identity.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _rate_check(identity: str) -> bool:
    """Return True if the identity is allowed to attempt auth; False if locked out."""
    return _rate_check_all([identity])


def _rate_record_fail(identity: str) -> None:
    """Record one failed auth attempt for the identity."""
    _rate_record_fail_all([identity])


def _rate_reset(identity: str) -> None:
    """Clear rate-limit history for an identity on successful auth."""
    _rate_reset_all([identity])


def _rate_client_ip() -> str:
    # X-Forwarded-For is client-controlled unless a trusted proxy normalizes it.
    return request.remote_addr or "unknown"


def _rate_keys(ip: str, username: str) -> list[str]:
    keys = [f"ip:{ip}"]
    exact_username = username.strip()
    if exact_username:
        keys.append(f"user:{exact_username}")
    return keys


def _rate_check_all(identities: list[str]) -> bool:
    identity_hashes = [_rate_identity_hash(identity) for identity in identities]
    if not identity_hashes:
        return True
    placeholders = ", ".join("?" for _ in identity_hashes)
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM auth_rate_limit_attempts WHERE attempted_at <= ?",
            (time.time() - _RATE_LIMIT_WINDOW_SEC,),
        )
        rows = conn.execute(
            f"""
            SELECT identity_hash, COUNT(*) AS attempt_count
            FROM auth_rate_limit_attempts
            WHERE identity_hash IN ({placeholders})
            GROUP BY identity_hash
            """,
            identity_hashes,
        ).fetchall()
    counts = {row["identity_hash"]: row["attempt_count"] for row in rows}
    return all(counts.get(identity_hash, 0) < _RATE_LIMIT_MAX_FAILS for identity_hash in identity_hashes)


def _rate_reserve_attempt_all(identities: list[str]) -> str | None:
    """Atomically reserve an auth attempt, or return None when any identity is locked."""
    identity_hashes = [_rate_identity_hash(identity) for identity in identities]
    attempt_id = secrets.token_hex(16)
    now = time.time()
    placeholders = ", ".join("?" for _ in identity_hashes)
    with _get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM auth_rate_limit_attempts WHERE attempted_at <= ?",
            (now - _RATE_LIMIT_WINDOW_SEC,),
        )
        rows = conn.execute(
            f"""
            SELECT identity_hash, COUNT(*) AS attempt_count
            FROM auth_rate_limit_attempts
            WHERE identity_hash IN ({placeholders})
            GROUP BY identity_hash
            """,
            identity_hashes,
        ).fetchall()
        counts = {row["identity_hash"]: row["attempt_count"] for row in rows}
        if any(counts.get(identity_hash, 0) >= _RATE_LIMIT_MAX_FAILS for identity_hash in identity_hashes):
            return None
        conn.executemany(
            """
            INSERT INTO auth_rate_limit_attempts (attempt_id, identity_hash, attempted_at, status)
            VALUES (?, ?, ?, 'pending')
            """,
            [(attempt_id, identity_hash, now) for identity_hash in identity_hashes],
        )
    return attempt_id


def _rate_release_attempt(attempt_id: str | None) -> None:
    if not attempt_id:
        return
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM auth_rate_limit_attempts WHERE attempt_id = ?",
            (attempt_id,),
        )


def _rate_finalize_attempt(attempt_id: str) -> None:
    with _get_db() as conn:
        conn.execute(
            "UPDATE auth_rate_limit_attempts SET status = 'failed' WHERE attempt_id = ?",
            (attempt_id,),
        )


def _rate_complete_success(attempt_id: str, identities: list[str]) -> None:
    identity_hashes = [_rate_identity_hash(identity) for identity in identities]
    placeholders = ", ".join("?" for _ in identity_hashes)
    with _get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM auth_rate_limit_attempts WHERE attempt_id = ?",
            (attempt_id,),
        )
        conn.execute(
            f"""
            DELETE FROM auth_rate_limit_attempts
            WHERE status = 'failed' AND identity_hash IN ({placeholders})
            """,
            identity_hashes,
        )


def _rate_record_fail_all(identities: list[str]) -> None:
    now = time.time()
    attempt_id = secrets.token_hex(16)
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM auth_rate_limit_attempts WHERE attempted_at <= ?",
            (now - _RATE_LIMIT_WINDOW_SEC,),
        )
        conn.executemany(
            """
            INSERT INTO auth_rate_limit_attempts (attempt_id, identity_hash, attempted_at, status)
            VALUES (?, ?, ?, 'failed')
            """,
            [(attempt_id, _rate_identity_hash(identity), now) for identity in identities],
        )


def _rate_reset_all(identities: list[str]) -> None:
    identity_hashes = [_rate_identity_hash(identity) for identity in identities]
    if not identity_hashes:
        return
    placeholders = ", ".join("?" for _ in identity_hashes)
    with _get_db() as conn:
        conn.execute(
            f"""
            DELETE FROM auth_rate_limit_attempts
            WHERE status = 'failed' AND identity_hash IN ({placeholders})
            """,
            identity_hashes,
        )

# ── DB setup ──────────────────────────────────────────────
AUTH_DB = data_path("auth.db")

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."

auth_bp = Blueprint("auth", __name__)

CSRF_HEADER_NAME = "X-Requested-With"
CSRF_HEADER_VALUE = "XMLHttpRequest"
CSRF_FORM_FIELD = "csrf_token"
CSRF_SESSION_KEY = "_auth_csrf_token"
_CSRF_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_EXEMPT_PATHS = {
    # Health Auto Export / Shortcuts webhook: authenticated by HEALTH_SYNC_TOKEN.
    "/api/apple-health/sync",
    # Stripe webhook: unauthenticated by session, authenticated by Stripe-Signature.
    "/webhook",
}
_PASSWORD_HASH_METHOD = "scrypt:32768:8:1"
_LEGACY_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_INVALID_OWNER_USER_ID = object()
_NO_LOGIN_OWNER_DB_ERROR = object()
_LOCAL_QA_ENABLED = "FITNESS_DASHBOARD_LOCAL_QA_ENABLED"
_LOCAL_QA_USERNAME = "FITNESS_DASHBOARD_LOCAL_QA_USERNAME"
_LOCAL_QA_PASSWORD = "FITNESS_DASHBOARD_LOCAL_QA_PASSWORD"
_owner_config_error_logged = False
_no_login_owner_error_logged = False


@contextmanager
def _get_db():
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _local_qa_enabled() -> bool:
    return os.environ.get(_LOCAL_QA_ENABLED, "").strip().lower() == "true"


def _table_exists(conn, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _owner_user_id_from_conn(conn):
    configured = os.environ.get("FITNESS_DASHBOARD_OWNER_USER_ID", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError:
            return _INVALID_OWNER_USER_ID
    row = conn.execute("SELECT MIN(id) FROM users").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _required_existing_owner_id(conn) -> int:
    owner_id = _owner_user_id_from_conn(conn)
    if owner_id is _INVALID_OWNER_USER_ID:
        raise RuntimeError("Local QA account requires a valid owner user ID")
    if owner_id is None or conn.execute(
        "SELECT 1 FROM users WHERE id = ?", (owner_id,)
    ).fetchone() is None:
        raise RuntimeError("Local QA account requires an existing owner")
    return owner_id


def _local_qa_user_id_from_conn(conn):
    if not _table_exists(conn, "local_qa_account"):
        return None
    row = conn.execute(
        "SELECT user_id FROM local_qa_account WHERE singleton = 1"
    ).fetchone()
    return int(row["user_id"]) if row else None


def _local_qa_user_id():
    if not _local_qa_enabled():
        return None
    with _get_db() as conn:
        return _local_qa_user_id_from_conn(conn)


def _is_local_qa_user_id(user_id) -> bool:
    if not _local_qa_enabled():
        return False
    try:
        candidate_id = int(user_id)
    except (TypeError, ValueError):
        return False
    with _get_db() as conn:
        qa_id = _local_qa_user_id_from_conn(conn)
        if qa_id != candidate_id:
            return False
        try:
            owner_id = _required_existing_owner_id(conn)
        except RuntimeError:
            return False
        return qa_id != owner_id


def data_user_id_for(user_id) -> int:
    candidate_id = int(user_id)
    if not _local_qa_enabled():
        return candidate_id
    with _get_db() as conn:
        qa_id = _local_qa_user_id_from_conn(conn)
        if candidate_id != qa_id:
            return candidate_id
        owner_id = _required_existing_owner_id(conn)
        if qa_id == owner_id:
            raise RuntimeError("Local QA mapping cannot resolve to the owner account itself")
        return owner_id


def _remove_local_qa_account(conn) -> None:
    if not _table_exists(conn, "local_qa_account"):
        return
    qa_id = _local_qa_user_id_from_conn(conn)
    if qa_id is not None:
        owner_id = _required_existing_owner_id(conn)
        if qa_id == owner_id:
            raise RuntimeError("Local QA mapping points to the owner; cleanup refused")
        conn.execute("DELETE FROM local_qa_account WHERE singleton = 1")
        conn.execute("DELETE FROM users WHERE id = ?", (qa_id,))
    conn.execute("DROP TABLE local_qa_account")


def _reconcile_local_qa_account(conn) -> None:
    if not _local_qa_enabled():
        _remove_local_qa_account(conn)
        return

    username = os.environ.get(_LOCAL_QA_USERNAME, "").strip()
    password = os.environ.get(_LOCAL_QA_PASSWORD, "")
    if not username or not password:
        raise RuntimeError("Local QA account requires username and password settings")
    if len(password) < 8:
        raise RuntimeError("Local QA account password must be at least 8 characters")
    owner_id = _required_existing_owner_id(conn)
    mapped = None
    if _table_exists(conn, "local_qa_account"):
        mapped = conn.execute(
            "SELECT user_id FROM local_qa_account WHERE singleton = 1"
        ).fetchone()
    if mapped is not None and int(mapped["user_id"]) == owner_id:
        raise RuntimeError("Local QA mapping points to the owner account")
    collision = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if collision is not None and (
        mapped is None or int(collision["id"]) != int(mapped["user_id"])
    ):
        raise RuntimeError("Local QA account username collides with an existing account")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS local_qa_account (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            user_id INTEGER NOT NULL UNIQUE
        )
        """
    )
    if mapped is not None:
        qa = conn.execute(
            "SELECT username, password FROM users WHERE id = ?",
            (mapped["user_id"],),
        ).fetchone()
        if qa is not None:
            password_hash = qa["password"]
            if not check_password_hash(password_hash, password):
                password_hash = _hash_password(password)
            if qa["username"] != username or password_hash != qa["password"]:
                conn.execute(
                    "UPDATE users SET username = ?, password = ?, salt = '' WHERE id = ?",
                    (username, password_hash, mapped["user_id"]),
                )
            return
        conn.execute("DELETE FROM local_qa_account WHERE singleton = 1")
    cursor = conn.execute(
        "INSERT INTO users (username, password, salt) VALUES (?, ?, ?)",
        (username, _hash_password(password), ""),
    )
    conn.execute(
        "INSERT INTO local_qa_account (singleton, user_id) VALUES (1, ?)",
        (cursor.lastrowid,),
    )


def init_auth_db():
    """Create users table if it doesn't exist; migrate columns if upgrading."""
    with _get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                username          TEXT    NOT NULL UNIQUE,
                password          TEXT    NOT NULL,
                salt              TEXT    NOT NULL,
                email             TEXT,
                is_pro            INTEGER NOT NULL DEFAULT 0,
                stripe_customer   TEXT,
                stripe_sub        TEXT,
                created           TEXT    DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_rate_limit_attempts (
                attempt_id   TEXT NOT NULL,
                identity_hash TEXT NOT NULL,
                attempted_at  REAL NOT NULL,
                status        TEXT NOT NULL
            )
            """
        )
        rate_limit_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(auth_rate_limit_attempts)")
        }
        if "attempt_id" not in rate_limit_columns:
            conn.execute("ALTER TABLE auth_rate_limit_attempts ADD COLUMN attempt_id TEXT")
            conn.execute(
                """
                UPDATE auth_rate_limit_attempts
                SET attempt_id = lower(hex(randomblob(16)))
                WHERE attempt_id IS NULL
                """
            )
        if "status" not in rate_limit_columns:
            conn.execute(
                "ALTER TABLE auth_rate_limit_attempts ADD COLUMN status TEXT NOT NULL DEFAULT 'failed'"
            )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS auth_rate_limit_attempts_identity_time_idx
            ON auth_rate_limit_attempts (identity_hash, attempted_at)
            """
        )
        # Migrate existing DBs that are missing the new columns
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        for col, definition in [
            ("email",           "TEXT"),
            ("is_pro",          "INTEGER NOT NULL DEFAULT 0"),
            ("stripe_customer", "TEXT"),
            ("stripe_sub",      "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        _reconcile_local_qa_account(conn)
        conn.commit()


def _legacy_hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def _hash_password(password: str) -> str:
    return generate_password_hash(password, method=_PASSWORD_HASH_METHOD)


def _is_legacy_password_hash(stored_hash: str) -> bool:
    return isinstance(stored_hash, str) and bool(_LEGACY_SHA256_RE.fullmatch(stored_hash))


def _verify_legacy_password(password: str, salt: str, stored_hash: str) -> bool:
    candidate = _legacy_hash_password(password, salt or "")
    return hmac.compare_digest(candidate, stored_hash)


# ── User model ────────────────────────────────────────────
class User(UserMixin):
    def __init__(self, id: int, username: str, email: str = None, is_pro: bool = False,
                 stripe_customer: str = None, stripe_sub: str = None):
        self.id = id
        self.username = username
        self.email = email
        self.is_pro = bool(is_pro)
        self.stripe_customer = stripe_customer
        self.stripe_sub = stripe_sub

    @staticmethod
    def _from_row(row):
        if not row:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            is_pro=bool(row["is_pro"]),
            stripe_customer=row["stripe_customer"],
            stripe_sub=row["stripe_sub"],
        )

    @staticmethod
    def get_by_id(user_id: int):
        with _get_db() as conn:
            row = conn.execute(
                "SELECT id, username, email, is_pro, stripe_customer, stripe_sub FROM users WHERE id = ?",
                (user_id,)
            ).fetchone()
        return User._from_row(row)

    @staticmethod
    def get_by_username(username: str):
        with _get_db() as conn:
            row = conn.execute(
                "SELECT id, username, email, is_pro, stripe_customer, stripe_sub FROM users WHERE username = ?",
                (username,)
            ).fetchone()
        return User._from_row(row)

    @staticmethod
    def authenticate(username: str, password: str):
        with _get_db() as conn:
            row = conn.execute(
                "SELECT id, username, password, salt, email, is_pro, stripe_customer, stripe_sub FROM users WHERE username = ?",
                (username,)
            ).fetchone()
            if not row:
                return None
            stored_hash = row["password"]
            if _is_legacy_password_hash(stored_hash):
                if not _verify_legacy_password(password, row["salt"], stored_hash):
                    return None
                conn.execute(
                    "UPDATE users SET password = ?, salt = ? WHERE id = ?",
                    (_hash_password(password), "", row["id"]),
                )
                return User._from_row(row)
            if check_password_hash(stored_hash, password):
                return User._from_row(row)
        return None

    @staticmethod
    def create(username: str, password: str, email: str = None):
        hashed = _hash_password(password)
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password, salt, email) VALUES (?, ?, ?, ?)",
                (username, hashed, "", email),
            )
            conn.commit()

    @staticmethod
    def mark_pro(user_id: int, stripe_customer: str = None, stripe_sub: str = None):
        """Upgrade user to Pro, optionally saving Stripe IDs."""
        with _get_db() as conn:
            conn.execute(
                "UPDATE users SET is_pro=1, stripe_customer=?, stripe_sub=? WHERE id=?",
                (stripe_customer, stripe_sub, user_id),
            )
            conn.commit()

    @staticmethod
    def revoke_pro(user_id: int):
        """Downgrade user from Pro (e.g. subscription cancelled)."""
        with _get_db() as conn:
            conn.execute(
                "UPDATE users SET is_pro=0, stripe_sub=NULL WHERE id=?",
                (user_id,)
            )
            conn.commit()


def _single_user_mode() -> bool:
    return os.environ.get("FITNESS_DASHBOARD_SINGLE_USER", "true").lower() != "false"


def _user_count() -> int:
    with _get_db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    return int(row[0] or 0)


def _owner_user_id():
    with _get_db() as conn:
        return _owner_user_id_from_conn(conn)


def _trusted_no_login_enabled() -> bool:
    return os.environ.get("FITNESS_DASHBOARD_NO_LOGIN", "").strip().lower() == "true"


def _trusted_no_login_request_host() -> bool:
    try:
        hostname = urlsplit(f"//{request.host}").hostname
    except ValueError:
        return False
    if not hostname:
        return False

    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".ts.net"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback or (
        isinstance(address, ipaddress.IPv4Address)
        and address in _TAILSCALE_IPV4_NETWORK
    )


def _trusted_no_login_owner():
    global _no_login_owner_error_logged

    try:
        owner_id = _owner_user_id()
        owner = None
        if owner_id is not _INVALID_OWNER_USER_ID and owner_id is not None:
            owner = User.get_by_id(owner_id)
    except sqlite3.Error:
        if not _no_login_owner_error_logged:
            logging.getLogger(__name__).exception(
                "FITNESS_DASHBOARD_NO_LOGIN=true but the owner account could not be read; "
                "normal authentication remains enabled"
            )
            _no_login_owner_error_logged = True
        return _NO_LOGIN_OWNER_DB_ERROR
    if owner is not None:
        return owner

    if not _no_login_owner_error_logged:
        logging.getLogger(__name__).error(
            "FITNESS_DASHBOARD_NO_LOGIN=true but no valid owner account could be loaded; "
            "normal authentication remains enabled"
        )
        _no_login_owner_error_logged = True
    return None


def _is_owner_user_id(user_id) -> bool:
    global _owner_config_error_logged

    if not _single_user_mode():
        return True
    owner_id = _owner_user_id()
    if owner_id is _INVALID_OWNER_USER_ID:
        if not _owner_config_error_logged:
            logging.getLogger(__name__).error(
                "FITNESS_DASHBOARD_OWNER_USER_ID is set but not an integer; "
                "owner-only routes are locked until fixed"
            )
            _owner_config_error_logged = True
        return False
    if owner_id is None:
        return True
    try:
        return int(user_id) == owner_id
    except (TypeError, ValueError):
        return False


def _has_owner_route_access(user_id) -> bool:
    return _is_owner_user_id(user_id) or _is_local_qa_user_id(user_id)


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))


def _safe_next(next_page: str | None, *, reload_root: bool = False) -> str:
    next_page = next_page or url_for("index")
    if any(ord(character) < 32 for character in next_page):
        next_page = url_for("index")
    if "\\" in next_page or not next_page.startswith("/") or next_page.startswith("//"):
        next_page = url_for("index")
    if reload_root and next_page == "/":
        next_page = "/?fd_shell_reload=20260525-fit181-controller-reload-r2"
    return next_page


# ── Routes ────────────────────────────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and current_user.is_authenticated:
        return redirect(_safe_next(request.args.get("next"), reload_root=True))

    if request.method == "POST":
        ip = _rate_client_ip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        rate_keys = _rate_keys(ip, username)
        attempt_id = None
        try:
            attempt_id = _rate_reserve_attempt_all(rate_keys)
            if not attempt_id:
                flash("Too many failed attempts. Please wait 10 minutes before trying again.")
                return render_template("login.html"), 429
            user = User.authenticate(username, password)
            if user:
                _rate_complete_success(attempt_id, rate_keys)
                login_user(user)
                return redirect(_safe_next(request.args.get("next")))
            _rate_finalize_attempt(attempt_id)
        except sqlite3.Error:
            current_app.logger.exception("Auth database error during login")
            try:
                _rate_release_attempt(attempt_id)
            except sqlite3.Error:
                current_app.logger.exception("Could not release failed login rate-limit reservation")
            flash("Login service temporarily unavailable. Please try again shortly.")
            return render_template("login.html"), 503
        flash("Invalid username or password.")
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if _single_user_mode() and _user_count() > 0:
        flash("Registration is disabled for this single-owner dashboard.")
        return render_template("login.html"), 403

    if request.method == "POST":
        ip = _rate_client_ip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip() or None
        rate_keys = _rate_keys(ip, username)
        attempt_id = None
        try:
            attempt_id = _rate_reserve_attempt_all(rate_keys)
            if not attempt_id:
                flash("Too many attempts. Please wait 10 minutes before trying again.")
                return render_template("login.html", register=True), 429
            if not username or not password:
                _rate_release_attempt(attempt_id)
                flash("Username and password are required.")
            elif len(password) < 8:
                _rate_release_attempt(attempt_id)
                flash("Password must be at least 8 characters.")
            elif User.get_by_username(username):
                _rate_finalize_attempt(attempt_id)
                flash("Username already taken.")
            else:
                User.create(username, password, email=email)
                user = User.authenticate(username, password)
                try:
                    _rate_complete_success(attempt_id, rate_keys)
                except sqlite3.Error:
                    # Account creation has already committed. Preserve the
                    # successful registration response rather than claiming
                    # failure after creating a single-owner account.
                    current_app.logger.exception(
                        "Could not clear registration rate-limit state after account creation"
                    )
                login_user(user)
                return redirect(url_for("index"))
        except sqlite3.Error:
            current_app.logger.exception("Auth database error during registration")
            try:
                _rate_release_attempt(attempt_id)
            except sqlite3.Error:
                current_app.logger.exception("Could not release failed registration rate-limit reservation")
            flash("Registration service temporarily unavailable. Please try again shortly.")
            return render_template("login.html", register=True), 503
    return render_template("login.html", register=True)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


# ── Global auth guard ─────────────────────────────────────
# Each entry is either a literal path (exact match) or a prefix ending in "/"
# (startswith match for subtree). Keep the HAE webhook exact-match so that
# `/api/apple-health/sync/status` still requires login — that endpoint can
# expose the sync token hint and last-sync metadata.
_PUBLIC_PREFIXES = (
    "/login", "/register", "/logout",
    "/landing",  # Separate dormant landing surface; FIT-297 owns its allowlist state.
    "/manifest.json", "/sw.js",
    "/static/",           # prefix — any static asset
    "/robots.txt", "/sitemap.xml",  # SEO crawlers
    # Stripe blueprint is dormant and intentionally unregistered; see FIT-299.
    "/api/apple-health/sync",   # exact — the POST webhook; its token is its auth
)


def _is_public(path: str) -> bool:
    for entry in _PUBLIC_PREFIXES:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def _is_csrf_exempt(path: str) -> bool:
    return path in _CSRF_EXEMPT_PATHS


def _has_csrf_header() -> bool:
    return request.headers.get(CSRF_HEADER_NAME) == CSRF_HEADER_VALUE


def _first_forwarded_header(name: str) -> str:
    return request.headers.get(name, "").split(",", 1)[0].strip()


def _origin_parts(value: str):
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed.scheme, parsed.netloc.lower()


def _expected_origin_parts() -> set[tuple[str, str]]:
    values = {request.host_url}
    public_base = os.environ.get("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "").strip()
    if public_base:
        values.add(public_base)

    forwarded_host = _first_forwarded_header("X-Forwarded-Host")
    if forwarded_host:
        forwarded_proto = _first_forwarded_header("X-Forwarded-Proto") or request.scheme
        if forwarded_proto in {"http", "https"}:
            values.add(f"{forwarded_proto}://{forwarded_host}")

    return {parts for value in values if (parts := _origin_parts(value))}


def _same_origin_url(value: str) -> bool:
    try:
        candidate = _origin_parts(value)
    except ValueError:
        return False
    return bool(candidate and candidate in _expected_origin_parts())


def _has_cross_origin_browser_header() -> bool:
    origin = request.headers.get("Origin", "").strip()
    if origin and not _same_origin_url(origin):
        return True
    return request.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site"


def _has_same_origin_browser_header() -> bool:
    if request.headers.get("Sec-Fetch-Site", "").strip().lower() == "same-origin":
        return True
    origin = request.headers.get("Origin", "").strip()
    if origin:
        return _same_origin_url(origin)
    referer = request.headers.get("Referer", "").strip()
    return bool(referer and _same_origin_url(referer))


def _form_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _has_valid_form_csrf_token() -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    submitted = request.form.get(CSRF_FORM_FIELD, "")
    return bool(expected and submitted and secrets.compare_digest(str(expected), submitted))


def _csrf_failure_response():
    if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({
            "error": "Forbidden",
            "code": "csrf_required",
            "message": f"Missing {CSRF_HEADER_NAME}: {CSRF_HEADER_VALUE}",
        }), 403
    return "Forbidden", 403


# ── Init helper (called from app.py) ─────────────────────
def init_auth(app):
    """Wire login_manager and auth blueprint into the Flask app."""
    from datetime import timedelta
    from flask import g, request, redirect, url_for
    from flask_login import current_user

    # SECRET_KEY resolution order:
    #   1) SECRET_KEY env var (explicit override)
    #   2) .flask-secret file in project dir (persisted across restarts)
    #   3) freshly generated 128-char hex secret, persisted to that file
    # Never fall back to a hard-coded default — that makes session cookies
    # forgeable by anyone with repo read access.
    _secret = os.environ.get("SECRET_KEY", "").strip()
    if not _secret:
        _secret_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask-secret")
        _secret = _load_or_create_secret(_secret_file)
    if not _secret or _secret == "dev-key-change-me":
        raise RuntimeError(
            "Refusing to start with default/empty SECRET_KEY. "
            "Set SECRET_KEY env var or allow .flask-secret generation in the project dir."
        )
    app.secret_key = _secret
    global _rate_limit_hmac_key
    _rate_limit_hmac_key = _secret.encode("utf-8")

    # ── Session hardening ────────────────────────────────
    app.config["SESSION_COOKIE_HTTPONLY"]  = True           # JS can't read the cookie
    app.config["SESSION_COOKIE_SAMESITE"]  = "Lax"          # CSRF mitigation
    # Default to Secure=true so session cookies refuse to ride over HTTP.
    # Local-dev HTTP (e.g. http://127.0.0.1:5050 without Tailscale TLS) can
    # opt back out by setting SESSION_COOKIE_SECURE=false explicitly.
    app.config["SESSION_COOKIE_SECURE"]    = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() != "false"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=14)
    app.config["REMEMBER_COOKIE_SECURE"]   = app.config["SESSION_COOKIE_SECURE"]

    login_manager.init_app(app)
    app.register_blueprint(auth_bp)
    init_auth_db()

    @app.before_request
    def load_trusted_no_login_owner():
        if (
            not _trusted_no_login_enabled()
            or not _trusted_no_login_request_host()
            or _has_cross_origin_browser_header()
        ):
            return None
        owner = _trusted_no_login_owner()
        if owner is _NO_LOGIN_OWNER_DB_ERROR:
            login_manager._update_request_context_with_user()
            return None
        if owner is None:
            return None
        login_manager._update_request_context_with_user(owner)
        g._trusted_no_login_owner = True
        return None

    @app.context_processor
    def inject_csrf_token():
        if getattr(g, "_trusted_no_login_owner", False):
            return {CSRF_FORM_FIELD: ""}
        return {CSRF_FORM_FIELD: _form_csrf_token()}

    @app.before_request
    def require_csrf_header():
        """Reject cross-site form posts while exempting token/signed webhooks."""
        if request.method not in _CSRF_MUTATING_METHODS:
            return None
        if _is_csrf_exempt(request.path):
            return None
        if _has_cross_origin_browser_header():
            return _csrf_failure_response()
        if _has_csrf_header() or _has_valid_form_csrf_token() or _has_same_origin_browser_header():
            return None
        return _csrf_failure_response()

    @app.before_request
    def require_login():
        """Globally protect all routes — redirect unauthenticated users to login."""
        if app.config.get("LOGIN_DISABLED"):
            return None
        if _is_public(request.path):
            return None
        if not current_user.is_authenticated:
            # JSON API callers get 401; browser callers get redirect
            if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
                from flask import jsonify
                return jsonify({"error": "Unauthorized", "login": "/login"}), 401
            return redirect(url_for("auth.login", next=request.path))
        if not getattr(g, "_trusted_no_login_owner", False) and not _has_owner_route_access(
            current_user.get_id()
        ):
            if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
                from flask import jsonify
                return jsonify({"error": "Forbidden"}), 403
            return "Forbidden", 403
