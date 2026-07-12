import fnmatch
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "support" / "runtime_artifact_policy.json"


def _rules(path):
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _matches(example, rule):
    normalized = example.strip("/")
    if rule.endswith("/"):
        directory = rule.strip("/")
        return normalized == directory or normalized.startswith(f"{directory}/")
    return fnmatch.fnmatchcase(normalized, rule.lstrip("/"))


def test_runtime_artifact_policy_covers_ignore_rules_and_hygiene_docs():
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    gitignore_rules = _rules(ROOT / ".gitignore")
    dockerignore_rules = _rules(ROOT / ".dockerignore")
    hygiene_doc = (ROOT / "docs" / "REPO_HYGIENE.md").read_text(encoding="utf-8").lower()

    assert policy["version"] == 1
    assert policy["default_policy"] == "keep_local_and_ignored"
    assert policy["deletion_policy"] == "explicit_approval_and_backup_restore_note_required"
    assert {item["id"] for item in policy["artifact_classes"]} >= {
        "whoop_database",
        "whoop_protected_material",
        "apple_health_database",
        "authentication_database",
        "ai_cache",
        "backup_bundles",
        "audit_bundles",
        "json_stores",
    }

    for artifact_class in policy["artifact_classes"]:
        git_rule = artifact_class["gitignore_rule"]
        docker_rule = artifact_class["dockerignore_rule"]
        assert git_rule in gitignore_rules, artifact_class["id"]
        assert docker_rule in dockerignore_rules, artifact_class["id"]
        assert artifact_class["documentation_marker"].lower() in hygiene_doc, artifact_class["id"]
        for example in artifact_class["examples"]:
            assert _matches(example, git_rule), (artifact_class["id"], example, git_rule)
            assert _matches(example, docker_rule), (artifact_class["id"], example, docker_rule)
