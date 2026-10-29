"""PRD pages must not present the removed Health Auto Export webhook or staleness watchdog as live."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STALE = [
    ("docs/prd/appendix/api-inventory.md", "/api/apple-health/sync"),
    ("docs/prd/15-ops-deployment.md", "/api/apple-health"),
    ("docs/prd/15-ops-deployment.md", "staleness watchdog"),
    ("docs/prd/appendix/page-relationships.md", "/api/apple-health"),
    ("docs/prd/appendix/enum-dictionary.md", "apple_health_parser"),
    ("docs/prd/appendix/enum-dictionary.md", "apple_health_sync.db"),
]


@pytest.mark.parametrize("doc, text", STALE)
def test_edited_docs_do_not_describe_hae_as_current(doc, text):
    assert text not in (ROOT / doc).read_text()
