"""The two ways in: GitHub login pinned to the owner's numeric id, or one hashed bot token.

Both verifiers run on every request to /mcp, before any method (tools, resources,
prompts) is dispatched, so a non-owner never reaches a handler.
"""
import hashlib
import hmac
import logging

import httpx2
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.utilities.token_cache import TokenCache

log = logging.getLogger("fitness_mcp.auth")
BOT_SCOPE = "fitness.read"


class OwnerGitHubVerifier(TokenVerifier):
    """Accepts a GitHub access token only when it belongs to `owner_id`.

    FastMCP's GitHubTokenVerifier accepts any GitHub user, so it is not used.
    """

    def __init__(self, owner_id: str, *, http_client: httpx2.AsyncClient | None = None, cache_seconds: int = 300):
        super().__init__(required_scopes=["read:user"])
        self._owner_id = str(owner_id)
        self._http = http_client or httpx2.AsyncClient(timeout=10)
        # A short cache keeps a GitHub 5xx from ending every session at once.
        self._cache = TokenCache(ttl_seconds=cache_seconds, max_size=64)
        # Refused non-owner tokens, by SHA-256: one GitHub call and one warning per token, not per request.
        self._refused = TokenCache(ttl_seconds=cache_seconds, max_size=64)

    async def verify_token(self, token: str) -> AccessToken | None:
        hit, cached = self._cache.get(token)
        if hit:
            return cached
        if self._refused.get(token)[0]:
            return None
        try:
            response = await self._http.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        except httpx2.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            user_id = str(response.json().get("id", ""))
        except (ValueError, AttributeError):
            return None
        if not (user_id.isascii() and user_id.isdigit()):
            return None  # a 200 without a usable id says nothing about the owner: do not cache it
        if not hmac.compare_digest(user_id, self._owner_id):
            log.warning("Rejected GitHub user id %s: not the owner", user_id[:20])
            self._refused.set(token, AccessToken(token="refused", client_id="", scopes=[]))
            return None
        result = AccessToken(
            token=token, client_id=user_id, scopes=["read:user"], subject=user_id,
            claims={"sub": user_id, "owner": True},
        )
        self._cache.set(token, result)
        return result


class BotTokenVerifier(TokenVerifier):
    """One static bearer for bots on the xAI API. Only its SHA-256 is configured."""

    def __init__(self, token_sha256: str, resource: str):
        super().__init__(required_scopes=[BOT_SCOPE])
        self._digest = token_sha256.strip().lower()
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(digest, self._digest):
            return None
        # token=digest so the raw bearer never lands in a repr or log line.
        return AccessToken(
            token=digest, client_id="fitness-bot", scopes=[BOT_SCOPE], resource=self._resource,
            claims={"owner": True},
        )
