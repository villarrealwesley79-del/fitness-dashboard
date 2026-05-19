# Testing

## No-live-network guard

The pytest suite blocks uncassetted external network access by default. The
autouse guard in `tests/conftest.py` patches socket connects and
`urllib.request.urlopen` so Nutritionix, USDA FDC, Anthropic vision, Open Food
Facts, and similar clients cannot accidentally hit live services in CI.

Loopback endpoints such as `127.0.0.1`, `::1`, and `localhost` remain allowed so
tests can exercise local Flask or LM Studio-style services.

When adding a client that talks to an external service:

1. Keep API keys in environment variables only.
2. Test request shape with a mock, cassette, or recorded fixture.
3. Do not rely on live network access in normal pytest or CI runs.
4. If a deliberately live diagnostic is ever needed, mark it with
   `@pytest.mark.allow_net` and keep it out of the default CI suite.

An unmocked external call fails with `BlockedNetworkError` and the endpoint that
was attempted. Fix that by mocking the transport or recording the cassette,
not by disabling the guard.
