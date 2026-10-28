"""Read-only MCP server for the fitness dashboard (PER-1585).

Run: python mcp/server.py   (all settings come from the environment; see docs/MCP.md)
"""
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
INSTRUCTIONS = (
    "Read-only view of the owner's training dashboard: today's plan, workout history, "
    "body trend and sleep. It never writes; block calendar time with your own calendar connector."
)


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
    host: str = "127.0.0.1"
    port: int = 8765

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
            host=env.get("MCP_LISTEN_HOST", "127.0.0.1").strip(),
            port=int(env.get("MCP_LISTEN_PORT", "8765")),
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


class LogRejectedRedirects:
    """Logs why /authorize or /register refused a client, so the owner can allowlist its redirect_uri."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path")
        if scope["type"] != "http" or path not in ("/authorize", "/register"):
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
                log.warning("%s refused: %s", path, detail[:300])
            await send(message)

        await self.app(scope, receive, spy)


def build_app(cfg: Config, *, github: httpx2.AsyncClient | None = None, dashboard_transport=None, storage=None):
    """Return (asgi_app, mcp_path). `github` and `dashboard_transport` exist for tests."""
    if not re.fullmatch(r"\d+", cfg.owner_github_id):
        raise SystemExit("MCP_OWNER_GITHUB_ID must be the numeric GitHub user id")
    if cfg.bot_token_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", cfg.bot_token_sha256):
        raise SystemExit("MCP_BOT_TOKEN_SHA256 must be 64 hex characters")

    public = urlsplit(cfg.public_url)
    origin, mcp_path = f"{public.scheme}://{public.netloc}", public.path or "/mcp"
    proxy = OAuthProxy(
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
        middleware=[Middleware(LogRejectedRedirects)],
    )
    return app, mcp_path


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    app, _ = build_app(cfg)
    # access_log off: /auth/callback?code=... would otherwise be written to the log.
    uvicorn.run(app, host=cfg.host, port=cfg.port, access_log=False)


if __name__ == "__main__":
    main()
