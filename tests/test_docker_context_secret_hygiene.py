from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

SENSITIVE_FILE_PATTERNS = {
    ".flask-secret",
    ".health-sync-token",
    ".apple-health-first-sync",
    ".env",
    ".env.*",
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite-*",
    "*.sqlite3",
    "*.sqlite3-*",
    "auth.db*",
    "*.bak*",
    "*.log",
    ".DS_Store",
}

SENSITIVE_PREFIXES = (
    ".git/",
    "audit-bundles/",
    "backup-bundles/",
    "docs/audits/",
)


def _dockerignore_lines() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _matches_dockerignore_rule(path: str, rule: str) -> bool:
    normalized = path.strip("/")
    normalized_rule = rule.strip()
    if not normalized_rule:
        return False

    if normalized_rule.startswith("**/"):
        recursive_rule = normalized_rule[3:]
        if _matches_dockerignore_rule(normalized, recursive_rule):
            return True
        parts = normalized.split("/")
        return any(
            _matches_dockerignore_rule("/".join(parts[index:]), recursive_rule)
            for index in range(1, len(parts))
        )

    if normalized_rule.endswith("/"):
        directory = normalized_rule.strip("/")
        return normalized == directory or normalized.startswith(f"{directory}/")

    rule_without_root = normalized_rule.lstrip("/")
    if "/" in rule_without_root:
        return fnmatch.fnmatchcase(normalized, rule_without_root)

    return fnmatch.fnmatchcase(normalized, rule_without_root)


def _matches_sensitive_pattern(path: str, pattern: str) -> bool:
    parts = path.strip("/").split("/")
    return any(fnmatch.fnmatchcase(part, pattern) for part in parts)


def _is_dockerignored(path: str) -> bool:
    return any(_matches_dockerignore_rule(path, rule) for rule in _dockerignore_lines())


def _docker_context_paths() -> list[str]:
    included: list[str] = []
    for current_root, dirnames, filenames in os.walk(ROOT):
        relative_root = Path(current_root).relative_to(ROOT)
        root_prefix = "" if str(relative_root) == "." else f"{relative_root}/"

        kept_dirnames = []
        for dirname in dirnames:
            relative_dir = f"{root_prefix}{dirname}"
            if _is_dockerignored(relative_dir):
                continue
            included.append(relative_dir)
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in filenames:
            relative_file = f"{root_prefix}{filename}"
            if not _is_dockerignored(relative_file):
                included.append(relative_file)
    return included


def assert_no_sensitive_paths(paths: Iterable[str]) -> None:
    offenders = []
    for path in paths:
        normalized = path.strip("/")
        if any(normalized.startswith(prefix) for prefix in SENSITIVE_PREFIXES):
            offenders.append(normalized)
            continue
        if any(_matches_sensitive_pattern(normalized, pattern) for pattern in SENSITIVE_FILE_PATTERNS):
            offenders.append(normalized)

    if offenders:
        raise AssertionError(f"sensitive paths included: {sorted(offenders)}")


def test_dockerignore_excludes_known_secret_and_token_files() -> None:
    for path in (".flask-secret", ".health-sync-token", ".apple-health-first-sync", ".env"):
        assert _is_dockerignored(path), f"{path} must be excluded from Docker context"

    for path in ("support/.flask-secret", "support/.health-sync-token", "support/.env.prod"):
        assert _is_dockerignored(path), f"{path} must be excluded from Docker context"


def test_docker_build_context_has_no_sensitive_files() -> None:
    assert_no_sensitive_paths(_docker_context_paths())


def test_manifest_guard_helper() -> None:
    try:
        assert_no_sensitive_paths([".flask-secret"])
    except AssertionError:
        pass
    else:
        raise AssertionError("expected .flask-secret to be reported as sensitive")

    assert_no_sensitive_paths(["app.py", "README.md"])
