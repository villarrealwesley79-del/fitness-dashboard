"""Read-only MCP server for the fitness dashboard (PER-1585).

Run: python mcp/server.py   (all settings come from the environment; see docs/MCP.md)
"""
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

import h11
import httpx2
from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import MultiAuth
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect
from starlette.responses import PlainTextResponse

from owner_auth import BotTokenVerifier, OwnerGitHubVerifier
from tools import Dashboard, register

log = logging.getLogger("fitness_mcp")

# Claude, ChatGPT and local clients. Grok's callbacks are not documented: connect once,
# read the rejected redirect_uri from the log, and add it via MCP_EXTRA_REDIRECT_URIS.
REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector/oauth/*",
    "http://localhost:*",
    "http://127.0.0.1:*",
)
# CIMD: a URL client_id makes this server fetch that URL. Only these hosts (and subdomains) may be fetched.
CIMD_HOSTS = ("claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "openai.com", "x.ai", "grok.com")
INSTRUCTIONS = (
    "Read-only view of the owner's training dashboard: today's plan, workout history, "
    "body trend and sleep. It never writes; block calendar time with your own calendar connector."
)


class VendorCIMDProxy(OAuthProxy):
    """OAuthProxy that fetches CIMD client documents only from `cimd_hosts` (fastmcp 4.1 has no such option).

    get_client is the one place /authorize, /token and /consent resolve a client_id.
    """

    def __init__(self, *args, cimd_hosts: tuple[str, ...], **kwargs):
        super().__init__(*args, **kwargs)
        self._cimd_hosts = tuple(h.lower() for h in cimd_hosts)

    def _fetchable(self, client_id: str) -> bool:
        """True only for a plain-ASCII URL on a vendor host and port 443 that IDNA can encode.

        FastMCP's fetcher raises on a bad port, a bad DNS label or a control character, which would be
        a 500 and a traceback in the log; this refuses them first.
        """
        try:
            if not re.fullmatch(r"[\x21-\x7e]+", client_id):
                return False
            parts = urlsplit(client_id)
            host = (parts.hostname or "").lower()
            host.encode("idna")
            if parts.port not in (None, 443):
                return False
        except ValueError:  # includes UnicodeError
            return False
        return any(host == h or host.endswith("." + h) for h in self._cimd_hosts)

    async def get_client(self, client_id: str):
        if self._cimd_manager is None or not self._cimd_manager.is_cimd_client_id(client_id):
            return await super().get_client(client_id)
        if not self._fetchable(client_id):
            return None  # refused before any fetch or store read
        try:
            return await super().get_client(client_id)
        except Exception:  # a FastMCP fetch bug must be a refusal, not a 500 plus a traceback in the log
            return None


@dataclass(frozen=True)
class Config:
    public_url: str
    github_client_id: str
    github_client_secret: str
    owner_github_id: str
    jwt_signing_key: str
    storage_dir: str
    storage_key: str
    read_token: str
    api_base: str = "http://127.0.0.1:5050"
    bot_token_sha256: str = ""
    extra_redirect_uris: tuple[str, ...] = ()
    extra_cimd_hosts: tuple[str, ...] = ()
    host: str = "127.0.0.1"
    port: int = 8765
    storage_max_bytes: int = 20 << 20
    storage_max_files: int = 2000

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        def need(name: str) -> str:
            value = env.get(name, "").strip()
            if not value:
                raise SystemExit(f"{name} is required")
            return value

        def secret(name: str) -> str:
            value = Path(need(name)).read_text(encoding="utf-8").strip()
            if not value:
                raise SystemExit(f"{name} points at an empty file")
            return value

        return cls(
            public_url=env.get("MCP_PUBLIC_BASE_URL", "https://frankbox.tail6c6490.ts.net:8443/mcp").strip(),
            github_client_id=need("MCP_GITHUB_CLIENT_ID"),
            github_client_secret=need("MCP_GITHUB_CLIENT_SECRET"),
            owner_github_id=need("MCP_OWNER_GITHUB_ID"),
            jwt_signing_key=secret("MCP_JWT_SIGNING_KEY_FILE"),
            storage_dir=need("MCP_STORAGE_DIR"),
            storage_key=secret("MCP_STORAGE_ENCRYPTION_KEY_FILE"),
            read_token=secret("FITNESS_READ_TOKEN_FILE"),
            api_base=env.get("FITNESS_API_BASE", "http://127.0.0.1:5050").strip(),
            bot_token_sha256=env.get("MCP_BOT_TOKEN_SHA256", "").strip(),
            extra_redirect_uris=tuple(u.strip() for u in env.get("MCP_EXTRA_REDIRECT_URIS", "").split(",") if u.strip()),
            extra_cimd_hosts=tuple(h.strip() for h in env.get("MCP_EXTRA_CIMD_HOSTS", "").split(",") if h.strip()),
            host=env.get("MCP_LISTEN_HOST", "127.0.0.1").strip(),
            port=int(env.get("MCP_LISTEN_PORT", "8765")),
            storage_max_bytes=int(env.get("MCP_STORAGE_MAX_BYTES", 20 << 20)),
            storage_max_files=int(env.get("MCP_STORAGE_MAX_FILES", 2000)),
        )


def make_storage(directory: str, key: str) -> FernetEncryptionWrapper:
    """OAuth client registrations and tokens at rest: Fernet-encrypted files in a 0700 dir."""
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    store = FileTreeStore(
        data_directory=root,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(root),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(root),
    )
    return FernetEncryptionWrapper(key_value=store, fernet=Fernet(key), raise_on_decryption_error=False)


def _is_stderr_file(path: str) -> bool:
    try:
        return os.path.samestat(os.fstat(2), os.stat(path))
    except OSError:
        return False


def rotate_log(path: str, cap: int) -> bool:
    """If the file behind fds 1 and 2 is over `cap` bytes, move it to `<path>.1` and point both at a fresh 0600 file.

    The cap holds for whatever writes to stderr (FastMCP, uvicorn, anything added later); disk use stays
    near 2 x cap plus what one 1 s check interval writes. It refuses to rename a path that is not fd 2's file.
    The new file is opened first: if that fails (no free fd, disk full) the log is truncated in place, so
    the cap holds and a later tick can still rotate.
    """
    if os.fstat(2).st_size <= cap or not _is_stderr_file(path):
        return False
    try:
        fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND, 0o600)
    except OSError:
        os.ftruncate(2, 0)
        return False
    try:
        os.replace(path, path + ".1")
        os.replace(path + ".new", path)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    return True


def rotate_log_forever(path: str, cap: int, interval: float = 1) -> None:
    while True:
        time.sleep(interval)
        try:
            rotate_log(path, cap)
        except OSError:  # the log is best effort; never stop the server over it
            pass


class LogRejectedRedirects:
    """Logs why /authorize or /register refused a client, so the owner can allowlist its redirect_uri.

    Also caps the four anonymous endpoints: /authorize, /register and every /consent render each leave
    a file that is never cleaned up, and FastMCP logs every failed /auth/callback at ERROR. A real
    callback is ~250 bytes of printable ASCII, so anything else gets a 400 before FastMCP can log it.
    These caps are the bound: the log file (rotate_log), the OAuth store (503 when full) and anonymous bodies (413).
    shortcut: one global fixed window, not per client (behind Funnel every peer is 127.0.0.1);
    a flood can lock the owner out of connecting for up to an hour. It writes one log line per window.
    """

    COUNTED = ("/authorize", "/register", "/auth/callback", "/consent")
    MAX_CALLBACK_QUERY = 1024
    MAX_BODY = 16 << 10  # anonymous request bodies; /mcp is authenticated and not limited

    def __init__(self, app, limit: int = 60, window: int = 3600, store: str | None = None,
                 store_max_bytes: int = 20 << 20, store_max_files: int = 2000):
        self.app, self.limit, self.window = app, limit, window
        self.started, self.count = time.monotonic(), 0
        self.store, self.store_max = store, (store_max_bytes, store_max_files)
        self.store_checked, self.store_full = -60.0, False

    def _store_is_full(self) -> bool:
        """Walk the OAuth store at most once a minute. Existing tokens keep working; only new connects stop."""
        if self.store and time.monotonic() - self.store_checked >= 60:
            files = size = 0

            def fail(error):  # os.walk ignores scandir errors; with no free fd it would walk nothing
                raise error

            try:
                for directory, _, names in os.walk(self.store, onerror=fail):
                    for name in names:
                        files += 1
                        try:
                            size += os.stat(os.path.join(directory, name)).st_size
                        except OSError:
                            pass
            except OSError:  # cannot look: keep the last answer, say nothing, try again on the next request
                return self.store_full
            self.store_checked = time.monotonic()
            full = size > self.store_max[0] or files > self.store_max[1]
            if full != self.store_full:  # one line per change, never per request
                log.warning("OAuth store %s", "is full: /register, /authorize and /consent answer 503" if full
                            else "is below its caps again")
            self.store_full = full
        return self.store_full

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in (*self.COUNTED, "/token"):
            return await self.counted(scope, receive, send)
        for name, value in scope["headers"]:
            if name == b"content-length" and value.isdigit() and int(value) > self.MAX_BODY:
                return await PlainTextResponse("Request body too large", status_code=413)(scope, receive, send)
        total, over, started = 0, False, False

        async def limited_receive():  # chunked bodies have no Content-Length: count them as they stream in
            nonlocal total, over
            message = await receive()
            if message["type"] == "http.request" and not over:
                total += len(message.get("body", b""))
                over = total > self.MAX_BODY
            return {"type": "http.disconnect"} if over else message

        async def guarded_send(message):
            nonlocal started
            if not over:
                started = started or message["type"] == "http.response.start"
                await send(message)

        try:
            await self.counted(scope, limited_receive, guarded_send)
        except Exception:
            if not over:
                raise
        if over and not started:
            await PlainTextResponse("Request body too large", status_code=413)(scope, receive, send)

    async def counted(self, scope, receive, send):
        path = scope.get("path")
        if scope["type"] != "http" or path not in self.COUNTED:
            return await self.app(scope, receive, send)
        if time.monotonic() - self.started >= self.window:
            self.started, self.count = time.monotonic(), 0
        self.count += 1
        if self.count > self.limit:
            if self.count == self.limit + 1:  # one line per window, or a flood grows the log without bound
                log.warning("%s rate limited: more than %d calls in %d s", path, self.limit, self.window)
            return await PlainTextResponse("Too many requests", status_code=429)(scope, receive, send)
        if path == "/auth/callback":
            query = scope["query_string"]
            if len(query) > self.MAX_CALLBACK_QUERY or re.search(r"[^\x20-\x7e]", unquote_plus(query.decode("latin-1"))):
                return await PlainTextResponse("Bad request", status_code=400)(scope, receive, send)
            return await self.app(scope, receive, send)
        if self._store_is_full():
            return await PlainTextResponse("Storage full", status_code=503)(scope, receive, send)
        if path == "/consent":
            return await self.app(scope, receive, send)
        refused = False

        async def spy(message):
            nonlocal refused
            if message["type"] == "http.response.start":
                refused = message["status"] == 400
            elif message["type"] == "http.response.body" and refused:
                refused = False
                if path == "/authorize":  # the 400 body is generic; the request carries the URI
                    detail = parse_qs(scope["query_string"].decode("latin-1")).get("redirect_uri", [""])[0]
                else:  # the registration error names the refused URI
                    detail = message.get("body", b"").decode("utf-8", "replace")
                log.warning("%s refused: %r", path, detail[:300])  # %r: a client cannot forge log lines with %0A
            await send(message)

        await self.app(scope, receive, spy)


class CatchUnhandled:
    """Outermost wrapper: any unhandled error becomes a bare 500 and at most one log line per window.

    Starlette's ServerErrorMiddleware re-raises, and uvicorn then prints a 100-line traceback for each
    request; on an uncounted anonymous route that would be an unbounded log flood.
    """

    def __init__(self, app, window: int = 3600):
        self.app, self.window, self.logged_at = app, window, None

    def __getattr__(self, name):  # app.state, app.routes: tests and callers use the Starlette app
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = done = False

        async def watch(message):
            nonlocal started, done
            started = started or message["type"] == "http.response.start"
            done = done or (message["type"] == "http.response.body" and not message.get("more_body"))
            await send(message)

        try:
            await self.app(scope, receive, watch)
        except Exception as exc:
            now = time.monotonic()
            noise = isinstance(exc, (ClientDisconnect, h11.LocalProtocolError))  # must not use up the hourly line
            if not noise and (self.logged_at is None or now - self.logged_at >= self.window):
                self.logged_at = now  # type and path only: never the message or the request content
                log.error("unhandled %s on %r", type(exc).__name__, str(scope.get("path"))[:100])
            try:
                if not started:
                    await PlainTextResponse("Internal Server Error", status_code=500)(scope, receive, send)
                elif not done:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            except Exception:  # the client is gone
                pass


def build_app(cfg: Config, *, github: httpx2.AsyncClient | None = None, dashboard_transport=None, storage=None):
    """Return (asgi_app, mcp_path). `github` and `dashboard_transport` exist for tests."""
    if not re.fullmatch(r"[0-9]+", cfg.owner_github_id):
        raise SystemExit("MCP_OWNER_GITHUB_ID must be the numeric GitHub user id")
    if cfg.bot_token_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", cfg.bot_token_sha256):
        raise SystemExit("MCP_BOT_TOKEN_SHA256 must be 64 hex characters")

    public = urlsplit(cfg.public_url)
    origin, mcp_path = f"{public.scheme}://{public.netloc}", public.path or "/mcp"
    proxy = VendorCIMDProxy(
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id=cfg.github_client_id,
        upstream_client_secret=cfg.github_client_secret,
        token_verifier=OwnerGitHubVerifier(cfg.owner_github_id, http_client=github),
        base_url=origin,
        allowed_client_redirect_uris=[*REDIRECT_URIS, *cfg.extra_redirect_uris],
        valid_scopes=["read:user", "offline_access"],
        jwt_signing_key=cfg.jwt_signing_key,
        client_storage=storage or make_storage(cfg.storage_dir, cfg.storage_key),
        cimd_hosts=(*CIMD_HOSTS, *cfg.extra_cimd_hosts),
    )
    verifiers = {"static": BotTokenVerifier(cfg.bot_token_sha256, cfg.public_url)} if cfg.bot_token_sha256 else {}
    # required_scopes=[]: the OAuth and bot tokens carry different scopes; each verifier is the gate.
    auth = MultiAuth(server=proxy, verifiers=verifiers, required_scopes=[])

    mcp = FastMCP("fitness", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)
    register(mcp, Dashboard(cfg.api_base, cfg.read_token, transport=dashboard_transport))
    host = public.hostname
    app = mcp.http_app(
        path=mcp_path,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=[host, f"{host}:*"],
        middleware=[Middleware(LogRejectedRedirects, store=cfg.storage_dir, store_max_bytes=cfg.storage_max_bytes,
                               store_max_files=cfg.storage_max_files)],
    )
    return CatchUnhandled(app), mcp_path


def main() -> None:
    import uvicorn

    os.umask(0o077)  # OAuth store subdirectories follow the umask; keep them private
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every outbound URL at INFO; an anonymous /token with a URL client_id would write it.
    for name in ("httpx2", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Noise reducers, not the bound: the log file cap below is.
    logging.getLogger("python_multipart").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.error").addFilter(lambda r: not r.getMessage().startswith("ASGI callable returned without"))
    if os.environ.get("MCP_LOG_FILE"):  # must match the plist's StandardErrorPath
        if not _is_stderr_file(os.environ["MCP_LOG_FILE"]):
            log.warning("MCP_LOG_FILE %r is not the file stderr writes to: the log is NOT capped", os.environ["MCP_LOG_FILE"])
        threading.Thread(target=rotate_log_forever, daemon=True, args=(
            os.environ["MCP_LOG_FILE"], int(os.environ.get("MCP_LOG_MAX_BYTES", 10 << 20)))).start()
    cfg = Config.from_env()
    app, _ = build_app(cfg)
    # access_log off: /auth/callback?code=... would otherwise be written to the log.
    uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False, log_level="error",
                limit_concurrency=100)  # 503 beyond 100 requests in flight: slow bodies cannot exhaust the fd table


if __name__ == "__main__":
    main()
