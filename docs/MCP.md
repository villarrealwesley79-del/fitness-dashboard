# Read-only MCP server (PER-1585)

Lets Claude, ChatGPT, the Grok app and Grok Bot, and your own bots on the xAI API read your training data. It never writes.

```
assistant cloud --HTTPS--> Tailscale Funnel :8443 --> MCP server 127.0.0.1:8765 --GET + read token--> Flask 127.0.0.1:5050
```

- Code: `mcp/` (FastMCP 4.1.4, its own process, launchd `com.fitness-mcp`). Flask only gains a small read-token hook in `auth.py`.
- Two credentials on the one endpoint `/mcp`: GitHub login pinned to your numeric GitHub id (OAuth, for the apps), and one static bearer token (for bots on the API).
- Tools (all `readOnlyHint`): `get_today_plan`, `get_workout_history`, `get_progressive_overload`, `get_body_trend`, `get_sleep_recovery`, `get_open_windows`. Each returns documented fields only.
- Status: built and tested locally. **Funnel stays off until the auth code is reviewed.** Nothing below has been run on frankbox.

## Setup (owner steps, on frankbox)

All secrets live in files with mode 0600 under `~/.fitness-mcp/`. Nothing secret goes in git.

```sh
mkdir -p ~/.fitness-mcp && chmod 700 ~/.fitness-mcp
cd ~/fitness-dashboard/mcp
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt   # python3.11 also works
openssl rand -hex 32 > ~/.fitness-mcp/jwt-signing-key
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > ~/.fitness-mcp/storage-key
chmod 600 ~/.fitness-mcp/*
```

### 1. GitHub OAuth App

1. GitHub > Settings > Developer settings > OAuth Apps > New OAuth App.
2. Homepage URL: `https://frankbox.tail6c6490.ts.net:8443`.
3. Authorization callback URL: `https://frankbox.tail6c6490.ts.net:8443/auth/callback` (FastMCP's default callback path; it is the public host from `MCP_PUBLIC_BASE_URL` plus `/auth/callback`).
4. Copy the client id, and generate a client secret.
5. Your numeric GitHub id: `curl -s https://api.github.com/users/<your-login> | grep '"id"'`. This is `MCP_OWNER_GITHUB_ID`. Only that account can sign in; any other GitHub user is refused.

### 2. Bot token (for bots on the xAI API)

```sh
TOKEN=$(openssl rand -hex 32)
printf '%s' "$TOKEN" | shasum -a 256 | cut -d' ' -f1    # -> MCP_BOT_TOKEN_SHA256
```

Put `$TOKEN` only in the bot's own config. The server stores just the hash. Leave `MCP_BOT_TOKEN_SHA256` empty to turn the static path off.

### 3. Flask read token

```sh
(umask 077; openssl rand -hex 32 > "$DATA_DIR/.mcp-read-token")
```

`DATA_DIR` is the dashboard's data directory. Flask accepts this token only for a fixed list of GET routes, only from a loopback peer with a loopback `Host`, and never when any `X-Forwarded-*`, `Forwarded` or `Tailscale-*` header is present (Funnel traffic also arrives from 127.0.0.1). If the file is missing, empty or readable by group/others, the feature is off. Point `FITNESS_READ_TOKEN_FILE` at the same file.

### 4. launchd

Copy `mcp/com.fitness-mcp.plist.example` to `~/Library/LaunchAgents/com.fitness-mcp.plist`, replace every `REPLACE_*`, `chmod 600` it (it holds the GitHub client secret), then:

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.fitness-mcp.plist
curl -si http://127.0.0.1:8765/mcp | head -1                       # HTTP/1.1 401, with a WWW-Authenticate header
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/.well-known/oauth-authorization-server   # 200
launchctl kickstart -k gui/$(id -u)/com.fitness-mcp                # restart; back within about 10 seconds
```

Environment: `MCP_PUBLIC_BASE_URL`, `MCP_LISTEN_HOST` (keep 127.0.0.1), `MCP_LISTEN_PORT`, `MCP_GITHUB_CLIENT_ID`, `MCP_GITHUB_CLIENT_SECRET`, `MCP_OWNER_GITHUB_ID`, `MCP_JWT_SIGNING_KEY_FILE`, `MCP_STORAGE_DIR`, `MCP_STORAGE_ENCRYPTION_KEY_FILE`, `MCP_BOT_TOKEN_SHA256`, `MCP_EXTRA_REDIRECT_URIS` (comma-separated), `MCP_EXTRA_CIMD_HOSTS` (comma-separated), `MCP_LOG_FILE` (the plist's `StandardErrorPath`), `MCP_LOG_MAX_BYTES` (default 10 MB), `MCP_STORAGE_MAX_BYTES` (default 20 MB), `MCP_STORAGE_MAX_FILES` (default 2000), `FITNESS_API_BASE`, `FITNESS_READ_TOKEN_FILE`.

### 5. Funnel (only after review)

1. On frankbox run `tailscale serve status` and confirm 8443 is free. Run `tailscale status --json` and check `Self.CapMap` has `funnel` and the funnel-ports capability, shields-up is off, and note whether the app is the Standalone or App Store variant (Tailscale's docs disagree on Funnel with Standalone; untested).
2. Admin console: turn on MagicDNS and HTTPS certificates. Access controls > Funnel > "Add Funnel to policy", and narrow `target` from `autogroup:member` to your login or a frankbox tag.
3. `tailscale funnel --bg --https=8443 http://127.0.0.1:8765`
4. Within about 10 minutes `dig @1.1.1.1 frankbox.tail6c6490.ts.net A` returns a public address.
5. Check `tailscale funnel status` lists only 8443, and from a phone on cellular: `/mcp` without a token returns 401, and `:5050` and `:3000` time out.

**Kill switch:** `tailscale funnel --https=8443 off`.

**Never** run `tailscale funnel --https=443 --set-path=/mcp` (Funnel is per host and port, so all of 443 would go public) and **never** run `tailscale funnel reset` (it wipes every serve entry, including the tailnet-only ones).

If a client refuses a `:8443` URL (no vendor documents whether it accepts one), move frankbox's tailnet 443 entry to another port and Funnel 443 for the MCP only.

## Add the connector

URL for all of them: `https://frankbox.tail6c6490.ts.net:8443/mcp`

- **Claude** (web; Desktop and mobile sync from it): claude.ai > Customize > Connectors > + Add > Add custom connector. Enter the URL, choose "Use Claude's published identity", "Sign in now", log in with GitHub and approve the consent page, then set the tools to Always allow. Auth settings can't be edited later; remove and re-add.
- **ChatGPT** (web only): chatgpt.com/plugins > + > Add custom MCP server. Name it, Connection = Server URL, Authentication = OAuth, tick the risk warning, create, install, sign in. Set its permission to "Allow read actions". Pro plans get read/fetch-only custom connectors, which is all this server offers.
- **Grok app:** grok.com/connectors > New Connector > Custom > URL > sign in.
- **Grok Bot:** ask the bot to add a custom MCP server with the URL, then finish OAuth on the connect card.
- **Bot on the xAI API:** pass the bearer as the tool's `authorization` field, for example `mcp(server_url=URL, server_label="fitness", allowed_tool_names=[...], authorization=os.environ["FITNESS_MCP_TOKEN"])`.
- **Grok's callback URLs are not documented.** On the first connect an unknown client is refused. Read `~/Library/Logs/fitness-mcp.log` for `/register refused:` or `/authorize refused:` lines, add that `redirect_uri` to `MCP_EXTRA_REDIRECT_URIS` only if its host is the vendor's own domain (`grok.com` or `x.ai`) and the line was logged at the moment you clicked Connect. Anyone on the internet can make this log say anything, so never add a URI you don't recognise. Then restart with `launchctl kickstart -k` and connect again. Allowed by default: Claude (`claude.ai`, `claude.com`), ChatGPT (`chatgpt.com/connector_platform_oauth_redirect`, `chatgpt.com/connector/oauth/*`) and localhost.

Tell the assistant to block calendar time with its own calendar connector. This server only reads.

## Revoke and rotate

| Goal | Do |
|---|---|
| Cut off bots on the API | Generate a new token, replace `MCP_BOT_TOKEN_SHA256`, restart |
| End every OAuth session | Replace `jwt-signing-key`, delete `MCP_STORAGE_DIR`, restart |
| Cut off one account's grant | Revoke the app at github.com/settings/applications. The server caches a successful GitHub check for 5 minutes, so access ends within 5 minutes |
| Rotate the Flask token | Rewrite `.mcp-read-token`, restart the MCP server (it reads the file at startup; Flask reads it per request) |
| Stop everything | Kill switch above, then `launchctl bootout gui/$(id -u)/com.fitness-mcp` |

## Known risks

- Health data reaches the vendors' clouds. ChatGPT may train on app data unless "Improve the model for everyone" is off. Anthropic's Messages API MCP connector is not eligible for zero data retention.
- Once Funnel is on the hostname is public (Certificate Transparency logs, scanners).
- FastMCP [#4265](https://github.com/PrefectHQ/fastmcp/issues/4265): refresh-token rotation has no grace window, so Claude can disconnect on refresh. [#4854](https://github.com/PrefectHQ/fastmcp/issues/4854): a GitHub 5xx can end sessions. Both open; pinned to 4.1.4.
- The host/origin guard refuses any request carrying a cross-origin `Origin` header (403), including discovery and `/register`. Vendor back ends don't send one, but a client that does OAuth from the browser would be blocked. If a first connect fails with 403, the fix is `allowed_origins` for that client's origin in `mcp/server.py`; not added speculatively.
- Tool output can carry prompt injection; read-only tools limit the damage.
- **Anonymous traffic is bounded where it lands, not per emitter.** Several endpoints are anonymous (`/register`, `/authorize`, `/consent`, `/auth/callback`, `/token`, metadata), and FastMCP, uvicorn and httpx each log in their own way, so the server caps the sinks:
  - **Log file: at most about 2 x `MCP_LOG_MAX_BYTES`** (default 10 MB, so about 20 MB). Every second a thread checks the size of the file behind stderr. Over the cap, it opens `<name>.new` first, then renames the log to `<name>.1` (replacing the old one), moves the new file into place and points stdout and stderr at it (0600). If the new file cannot be opened (no free file descriptor, disk full), it truncates the log in place instead, so the cap still holds and the next check can rotate. This needs `MCP_LOG_FILE` set to the same path as the plist's `StandardOutPath`/`StandardErrorPath`; unset, nothing rotates, and a path that is not the file stderr writes to logs a warning at startup and is never renamed. The cap also allows whatever one 1 s interval writes.
  - **Requests in flight: at most 100** (uvicorn `limit_concurrency`; beyond that new requests get 503), so slow-body POSTs cannot use up the file descriptors the log rotation needs. Known limitation: a socket that connects but never sends a request head still counts toward the 100 for everyone else, so 100 idle sockets make every client, including the owner, get 503 until they close. Such a socket is not itself refused and uvicorn has no timeout for it, so enough of them can still use up the process's file descriptors. The log rotation and the store check therefore tolerate a full descriptor table (see above), but new requests are refused until the sockets close or the server is restarted.
  - **OAuth store: at most `MCP_STORAGE_MAX_BYTES` and `MCP_STORAGE_MAX_FILES`.** `/register`, `/authorize` and every `/consent` render leave files that FastMCP never cleans up. Over either cap (checked once a minute) those three paths answer 503 "Storage full". Existing logins and the bot token keep working; only new connects stop. The server logs one line when the store becomes full and one when it recovers. To recover, stop the server and delete `MCP_STORAGE_DIR` (every client then reconnects once), or raise the caps.
  - **Anonymous bodies: at most 16 KB.** A `POST` to `/register`, `/authorize`, `/consent`, `/auth/callback` or `/token` with a larger `Content-Length`, or a chunked body that streams past 16 KB on a route that reads the body, gets 413. Other routes (for example `/token` with a JSON body) answer 401 without reading the body and discard it, with memory flat. `/mcp` is authenticated and not limited.
  - **Rate window:** `/register`, `/authorize`, `/consent` and `/auth/callback` share one global window of 60 calls an hour and answer 429 after that (behind Funnel every peer looks like 127.0.0.1), so a flood can block a new connect for up to an hour; existing connections keep working. `/token` is not counted, so a flood cannot lock out the owner's token refreshes. A callback longer than 1 KB or containing anything but printable ASCII gets a 400 before FastMCP sees it. Malformed URL `client_id`s are refused before FastMCP sees them.
  - **Noise reducers (not the bound):** the plist sets `FASTMCP_LOG_LEVEL=ERROR`; `main()` sets `httpx2`, `httpx` and `httpcore` to WARNING and `python_multipart` to ERROR, filters uvicorn's "ASGI callable returned without completing response" line, and runs uvicorn at `error`. The trade-off is that FastMCP warnings such as upstream refresh failures are not logged either.
  - **Backstop:** an outermost wrapper turns any unhandled error into a bare 500 and logs at most one `unhandled <ExceptionType> on <path>` line an hour (client disconnects do not use it up). The path is client-chosen, so it is repr-escaped and cut to 100 characters; the exception message and request content are never logged.
- **Client ID metadata documents (CIMD).** A client may send a URL as its `client_id`, and this server then fetches that URL (FastMCP caps it at 5 KB, no redirects, no private addresses). To stop anyone making frankbox fetch arbitrary pages, only these hosts and their subdomains are fetched as the `client_id` URL, on port 443: `claude.ai`, `claude.com`, `anthropic.com`, `chatgpt.com`, `openai.com`, `x.ai`, `grok.com`. Any other URL `client_id` is refused as an unknown client on `/authorize`, `/token` and `/consent`, before any fetch. Registered (DCR) client ids are not URLs and are unaffected. The allowlist covers the `client_id` URL only: a vendor-hosted document can name a `jwks_uri` on any host, which FastMCP then fetches (with its private-address and size protections). If a client that should work is refused, find the host of its `client_id` URL in the vendor's connector docs, add it to `MCP_EXTRA_CIMD_HOSTS` (comma-separated, e.g. `mcp.example.com`; subdomains are included), restart with `launchctl kickstart -k`, and connect again. Refused hosts are not logged (with the plist's `FASTMCP_LOG_LEVEL=ERROR`), so a flood cannot grow the log. Setting `MCP_EXTRA_CIMD_HOSTS=*` allows any host as the `client_id` URL (still https, port 443, plain ASCII). FastMCP's fetcher still refuses private, loopback, link-local and multicast addresses, redirect URIs stay on the allowlist (`MCP_EXTRA_REDIRECT_URIS`), only the owner's GitHub id can finish a login, and the consent page shows the client's host as "Verified domain" (that only says where the document was fetched from, not who runs it), so check it before you approve; on a phone, scroll the Verified domain line to its end and read the host right to left. **What `*` gives up:** anyone can make frankbox fetch any public https URL (5 KB, no redirects, up to about 30 s) without signing in, `/token` is not rate-limited, and the fetched host sees frankbox's public IP. The first sentence of this paragraph (only vendor hosts are fetched) holds only without `*`.
- Open Wearables' own MCP is deliberately not proxied: it is stdio-only and unauthenticated, and its API key can delete users and workouts.

## Tests

```sh
python -m pytest tests/test_mcp_read_token.py -q          # Flask side, part of the normal suite
python3.11 -m venv mcp/.venv && mcp/.venv/bin/pip install -r mcp/requirements.txt
mcp/.venv/bin/python -m pytest mcp/tests -q                # MCP side (CI installs mcp/requirements.txt)
```
