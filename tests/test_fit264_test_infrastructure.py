"""FIT-264 regression contracts for shared test and CI infrastructure."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _assigned_names(node):
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {
        child.id
        for target in targets
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    }


def _referenced_names(node):
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _frontend_path_names(tree):
    names = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        expression = ast.unparse(node.value).lower()
        if any(suffix in expression for suffix in (".js", ".html", ".css")):
            names.update(_assigned_names(node))
    return names


def _reads_frontend_source(node, frontend_path_names):
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "read_text"
        and (
            any(suffix in ast.unparse(child).lower() for suffix in (".js", ".html", ".css"))
            or _referenced_names(child.func.value) & frontend_path_names
        )
        for child in ast.walk(node)
    )


def _walk_lexical_scope(scope):
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_lexical_scope(child)


def _frontend_source_factories(tree, frontend_path_names):
    factories = set()
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    changed = True
    while changed:
        changed = False
        for function in functions:
            nodes = list(_walk_lexical_scope(function))
            source_names = _frontend_source_provenance(
                nodes, factories, frontend_path_names
            )
            returns_frontend_source = any(
                _referenced_names(node.value) & source_names
                or _reads_frontend_source(node.value, frontend_path_names)
                for node in nodes
                if isinstance(node, ast.Return) and node.value
            )
            if returns_frontend_source and function.name not in factories:
                factories.add(function.name)
                changed = True
    return factories


def _frontend_source_provenance(nodes, source_factories, frontend_path_names):
    source_names = {"APP_JS", "APP_HTML", "APP_LOADER_JS", "APP_SW"}

    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if _reads_frontend_source(node.value, frontend_path_names):
            source_names.update(_assigned_names(node))

    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            calls_source_factory = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id in source_factories
                for child in ast.walk(node.value)
            )
            assigned = _assigned_names(node)
            if assigned - source_names and (
                _referenced_names(node.value) & source_names or calls_source_factory
            ):
                source_names.update(assigned)
                changed = True

    return source_names


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
        frontend_path_names = _frontend_path_names(tree)
        source_factories = _frontend_source_factories(tree, frontend_path_names)
        scopes = [tree, *(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )]
        for scope in scopes:
            scope_nodes = list(_walk_lexical_scope(scope))
            frontend_source_names = _frontend_source_provenance(
                scope_nodes, source_factories, frontend_path_names
            )
            for node in scope_nodes:
                if not isinstance(node, ast.Assert):
                    continue
                if not (
                    _referenced_names(node.test) & frontend_source_names
                    or _reads_frontend_source(node.test, frontend_path_names)
                ):
                    continue
                if any(
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and "\n" in child.value
                    for child in ast.walk(node.test)
                ):
                    offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []
