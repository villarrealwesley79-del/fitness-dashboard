"""FIT-264 regression contracts for shared test and CI infrastructure."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_app_bootstrap_uses_pytest_tmp_path():
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    meal_tests = (ROOT / "tests" / "test_meal_intake_api.py").read_text(encoding="utf-8")

    assert "def isolated_app(" in conftest
    assert "tmp_path" in conftest.split("def isolated_app(", 1)[1].split("\n\n", 1)[0]
    assert 'SECRET_KEY="fitness-dashboard-pytest-secret"' in conftest
    assert "tempfile.mkdtemp" not in meal_tests
    assert "def _client(" not in meal_tests


def test_named_date_contracts_do_not_read_wall_clock_directly():
    for relative_path in (
        "tests/test_food_logs_by_date.py",
        "tests/test_nutrition_history_breakdown.py",
        "tests/test_meal_intake_api.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "datetime.now()" not in source, relative_path
        assert "module.datetime.now()" not in source, relative_path


def test_frontend_contracts_do_not_embed_indentation_sensitive_assertions():
    offenders = []
    for path in (ROOT / "tests").glob("test_*.py"):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            if any(
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and "\n" in child.value
                for child in ast.walk(node.test)
            ):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
