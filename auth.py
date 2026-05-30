"""
auth.py — Flask-Login authentication module for Fitness Dashboard.
SQLite-backed, no SQLAlchemy. Minimal proof-of-concept for SaaS productization.
"""

import os
import sqlite3
import hmac
import hashlib
import re
import secrets
import time
from collections import defaultdict
from contextlib import contextmanager
from flask import Blueprint, current_app, request, redirect, url_for, render_template, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required
from runtime_config import data_path
from werkzeug.security import check_password_hash, generate_password_hash

# ── Rate limiting (in-memory, per-IP) ────────────────────
# Tracks failed auth attempts: {ip: [(timestamp, ...), ...]}
_RATE_LIMIT_WINDOW_SEC = 600   # 10 minutes
_RATE_LIMIT_MAX_FAILS  = 10    # max failures before lockout
_rate_fail_log: dict = defaultdict(list)


def _rate_check(ip: str) -> bool:
    """Return True if the IP is allowed to attempt auth; False if locked out."""
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW_SEC
    # Prune old entries
    _rate_fail_log[ip] = [t for t in _rate_fail_log[ip] if t > window_start]
    return len(_rate_fail_log[ip]) < _RATE_LIMIT_MAX_FAILS


def _rate_record_fail(ip: str) -> None:
    """Record one failed auth attempt for the IP."""
    _rate_fail_log[ip].append(time.time())


def _rate_reset(ip: str) -> None:
    """Clear rate-limit history for an IP on successful login."""
    _rate_fail_log.pop(ip, None)

# ── DB setup ──────────────────────────────────────────────
AUTH_DB = data_path("auth.db")

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."

auth_bp = Blueprint("auth", __name__)

_PASSWORD_HASH_METHOD = "scrypt:32768:8:1"
_LEGACY_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


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
    configured = os.environ.get("FITNESS_DASHBOARD_OWNER_USER_ID", "").strip()
    if configured:
        try:
            return int(configured)
        except ValueError:
            return None
    with _get_db() as conn:
        row = conn.execute("SELECT MIN(id) FROM users").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _is_owner_user_id(user_id) -> bool:
    if not _single_user_mode():
        return True
    owner_id = _owner_user_id()
    if owner_id is None:
        return True
    try:
        return int(user_id) == owner_id
    except (TypeError, ValueError):
        return False


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))


# ── Routes ────────────────────────────────────────────────
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        if not _rate_check(ip):
            flash("Too many failed attempts. Please wait 10 minutes before trying again.")
            return render_template("login.html"), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            user = User.authenticate(username, password)
        except sqlite3.Error:
            current_app.logger.exception("Auth database error during login")
            flash("Login service temporarily unavailable. Please try again shortly.")
            return render_template("login.html"), 503
        if user:
            _rate_reset(ip)
            login_user(user)
            next_page = request.args.get("next") or url_for("index")
            return redirect(next_page)
        _rate_record_fail(ip)
        flash("Invalid username or password.")
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if _single_user_mode() and _user_count() > 0:
        flash("Registration is disabled for this single-owner dashboard.")
        return render_template("login.html"), 403

    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        if not _rate_check(ip):
            flash("Too many attempts. Please wait 10 minutes before trying again.")
            return render_template("login.html", register=True), 429
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip() or None
        if not username or not password:
            flash("Username and password are required.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif User.get_by_username(username):
            _rate_record_fail(ip)
            flash("Username already taken.")
        else:
            User.create(username, password, email=email)
            _rate_reset(ip)
            user = User.authenticate(username, password)
            login_user(user)
            return redirect(url_for("index"))
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
    "/landing", "/pricing",
    "/manifest.json", "/sw.js",
    "/static/",           # prefix — any static asset
    "/robots.txt", "/sitemap.xml",  # SEO crawlers
    "/webhook",            # Stripe webhook — must be unauthenticated
    "/success", "/cancel", # Post-checkout pages
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


# ── Init helper (called from app.py) ─────────────────────
def init_auth(app):
    """Wire login_manager and auth blueprint into the Flask app."""
    from datetime import timedelta
    from flask import request, redirect, url_for
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
        try:
            if os.path.exists(_secret_file):
                with open(_secret_file) as _fh:
                    _secret = _fh.read().strip()
        except Exception:
            _secret = ""
        if not _secret:
            _secret = secrets.token_hex(64)
            try:
                with open(_secret_file, "w") as _fh:
                    _fh.write(_secret)
                os.chmod(_secret_file, 0o600)
                print(f"INFO: generated new Flask SECRET_KEY and persisted to {_secret_file}")
            except Exception as _exc:
                print(f"WARN: could not persist SECRET_KEY to {_secret_file}: {_exc}")
    if not _secret or _secret == "dev-key-change-me":
        raise RuntimeError(
            "Refusing to start with default/empty SECRET_KEY. "
            "Set SECRET_KEY env var or allow .flask-secret generation in the project dir."
        )
    app.secret_key = _secret

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
        if not _is_owner_user_id(current_user.get_id()):
            if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
                from flask import jsonify
                return jsonify({"error": "Forbidden"}), 403
            return "Forbidden", 403
