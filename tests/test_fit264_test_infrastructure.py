"""FIT-264 regression contracts for shared test and CI infrastructure."""

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]

# Source assertions against app.js are unsafe for behavioral contracts: they
# can pass while the browser behavior is broken.  Keep this allowlist exact and
# small.  Every entry is a complete (relative test path, test function,
# literal) tuple; there are no wildcards or semantic patterns.
APP_JS_SOURCE_ASSERTION_ALLOWLIST = {
    (
        "tests/test_fit145_offline_queue.py",
        "test_meal_queue_uses_versioned_indexeddb_schema",
        "const MEAL_QUEUE_DB_NAME = 'fitMealIntakeQueueDB';",
    ): "Stable IndexedDB database name contract.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_meal_queue_uses_versioned_indexeddb_schema",
        "const MEAL_QUEUE_DB_VERSION = 1;",
    ): "Stable IndexedDB schema version contract.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_meal_queue_uses_versioned_indexeddb_schema",
        "const MEAL_QUEUE_STORE = 'queued_meals';",
    ): "Stable IndexedDB metadata store name.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_meal_queue_uses_versioned_indexeddb_schema",
        "const MEAL_PHOTO_STORE = 'meal_photos';",
    ): "Stable IndexedDB photo store name.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_sync_ui_includes_meal_retry_discard_and_privacy_copy",
        "data-meal-sync-retry",
    ): "Stable generated retry control attribute.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_sync_ui_includes_meal_retry_discard_and_privacy_copy",
        "data-meal-sync-discard",
    ): "Stable generated discard control attribute.",
    (
        "tests/test_fit145_offline_queue.py",
        "test_sync_ui_includes_meal_retry_discard_and_privacy_copy",
        "Meal · ${escapeHtml(titleText)}",
    ): "Stable generated meal row label markup.",
    (
        "tests/test_fit145_offline_queue.py",
        "_fit145_block",
        "// FIT-145: meal-intake offline queue.",
    ): "Stable FIT-145 source block boundary marker.",
    (
        "tests/test_fit145_offline_queue.py",
        "_fit145_block",
        "function completeWorkout()",
    ): "Stable FIT-145 source block boundary marker.",
    (
        "tests/test_fit145_offline_queue.py",
        "_fit145_storage_block",
        "function mealQueueRequest(",
    ): "Stable FIT-145 storage source block boundary marker.",
    (
        "tests/test_fit145_offline_queue.py",
        "_fit145_storage_block",
        "function renderSyncBanner()",
    ): "Stable FIT-145 storage source block boundary marker.",
    (
        "tests/test_fit145_offline_queue.py",
        "_app_js_block",
        "<indirect:start>",
    ): "Legacy generic source block helper validates caller markers.",
    (
        "tests/test_personal_vocab.py",
        "test_settings_ui_does_not_expose_learned_vocabulary_card",
        "/api/personal-vocab",
    ): "Stable negative UI endpoint exposure contract.",
    (
        "tests/test_workout_sync_queue_js.py",
        "test_frontend_asset_versions_stay_in_sync",
        "const ACTIVE_WORKOUT_DRAFT_KEY = 'fit168:active-workout-draft:v1';",
    ): "Stable active-workout draft storage key.",
    (
        "tests/test_workout_sync_queue_js.py",
        "test_frontend_asset_versions_stay_in_sync",
        "const ACTIVE_WORKOUT_DRAFT_VERSION = 1;",
    ): "Stable active-workout draft schema version.",
    (
        "tests/test_exercise_library_preferences.py",
        "test_settings_ui_exposes_profile_fields",
        "date_of_birth",
    ): "Stable settings profile field key.",
    (
        "tests/test_exercise_library_preferences.py",
        "test_settings_ui_exposes_profile_fields",
        "sex_options",
    ): "Stable settings profile options key.",
}


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
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        expression = ast.unparse(node.value).lower()
        # This guard intentionally covers only the app.js behavior surface.
        # HTML, CSS, docs, backend source, and the service worker have their
        # own static-contract reasons and must not be tainted by this audit.
        if "app.js" in expression:
            names.update(_assigned_names(node))
    return names


def _reads_frontend_source(node, frontend_path_names):
    def is_frontend_path(expression):
        return (
            "app.js" in ast.unparse(expression).lower()
            or bool(_referenced_names(expression) & frontend_path_names)
        )

    def opens_frontend_path(call):
        if not isinstance(call, ast.Call):
            return False
        if isinstance(call.func, ast.Attribute) and call.func.attr == "open":
            return is_frontend_path(call.func.value)
        return (
            isinstance(call.func, ast.Name)
            and call.func.id == "open"
            and bool(call.args)
            and is_frontend_path(call.args[0])
        )

    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr in {"read_text", "read_bytes"} and is_frontend_path(child.func.value):
            return True
        if child.func.attr == "read" and opens_frontend_path(child.func.value):
            return True
    return False


def _verified_runtime_call_names(tree):
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "js_runtime":
            for alias in node.names:
                if alias.name == "run_app_js":
                    imported.add(alias.asname or alias.name)
    rebound = {
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        for name in _assigned_names(node)
    }
    rebound.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    rebound.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
    return imported - rebound


def _shared_runtime_source_imports(tree):
    imported_names = set()
    module_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "js_runtime":
            for alias in node.names:
                if alias.name == "APP_JS":
                    imported_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "js_runtime":
                    module_names.add(alias.asname or alias.name)
    return imported_names, module_names


def _references_shared_runtime_source(node, imported_names, module_names):
    return bool(_referenced_names(node) & set(imported_names)) or any(
        isinstance(child, ast.Attribute)
        and child.attr == "APP_JS"
        and isinstance(child.value, ast.Name)
        and child.value.id in module_names
        for child in ast.walk(node)
    )


def _is_direct_runtime_call(call, runtime_call_names):
    function = call.func
    return isinstance(function, ast.Name) and function.id in runtime_call_names


def _is_runtime_source_expression(expression, runtime_names, runtime_call_names):
    if isinstance(expression, ast.Name):
        return expression.id in runtime_names
    if isinstance(expression, (ast.Attribute, ast.Subscript)):
        return _is_runtime_source_expression(
            expression.value, runtime_names, runtime_call_names
        )
    return isinstance(expression, ast.Call) and _is_direct_runtime_call(
        expression, runtime_call_names
    )


def _is_runtime_output_expression(expression, runtime_names=(), runtime_call_names=()):
    """Return True only for values proven to come from a runtime execution."""
    runtime_names = set(runtime_names)
    if _is_runtime_source_expression(expression, runtime_names, runtime_call_names):
        return True
    if not isinstance(expression, ast.Call):
        return False
    function = expression.func
    if (
        isinstance(function, ast.Attribute)
        and _is_runtime_source_expression(
            function.value, runtime_names, runtime_call_names
        )
    ):
        return True
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "json"
        and function.attr == "loads"
        and any(
            _is_runtime_source_expression(arg, runtime_names, runtime_call_names)
            for arg in expression.args
        )
    )


def _runtime_output_names(nodes, runtime_call_names):
    names = set()
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            if _is_runtime_output_expression(node.value, names, runtime_call_names):
                before = len(names)
                names.update(_assigned_names(node))
                changed |= len(names) != before
    return names


def _walk_lexical_scope(scope):
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_lexical_scope(child)


def _frontend_source_factories(
    tree, frontend_path_names, runtime_call_names,
    imported_source_names=(), runtime_module_names=(),
):
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
                nodes, factories, frontend_path_names, runtime_call_names,
                imported_source_names, runtime_module_names,
            )
            returns_frontend_source = any(
                _referenced_names(node.value) & source_names
                or _reads_frontend_source(node.value, frontend_path_names)
                or _references_shared_runtime_source(
                    node.value, imported_source_names, runtime_module_names
                )
                for node in nodes
                if isinstance(node, ast.Return) and node.value
            )
            if returns_frontend_source and function.name not in factories:
                factories.add(function.name)
                changed = True
    return factories


def _frontend_source_provenance(
    nodes, source_factories, frontend_path_names, runtime_call_names,
    initial_source_names=(), runtime_module_names=(),
):
    source_names = {"APP_JS", *initial_source_names}

    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        if (
            _reads_frontend_source(node.value, frontend_path_names)
            or _references_shared_runtime_source(
                node.value, initial_source_names, runtime_module_names
            )
        ):
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
                _referenced_names(node.value) & source_names
                or calls_source_factory
                or _references_shared_runtime_source(
                    node.value, initial_source_names, runtime_module_names
                )
            ):
                source_names.update(assigned)
                changed = True

    return source_names


def _function_name_for_line(tree, lineno):
    """Return the innermost function containing an assertion line."""
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= lineno <= getattr(node, "end_lineno", node.lineno)
    ]
    if not candidates:
        return "<module>"
    return max(candidates, key=lambda node: (node.lineno, -node.end_lineno)).name


def _resolved_string_bindings(nodes):
    """Resolve simple literal needles used indirectly in source assertions."""
    bindings = {}

    def values(expression):
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}
        if isinstance(expression, ast.Name):
            return set(bindings.get(expression.id, ()))
        if isinstance(expression, (ast.List, ast.Set, ast.Tuple)):
            resolved = set()
            for element in expression.elts:
                resolved.update(values(element))
            return resolved
        if isinstance(expression, ast.Call):
            return {
                child.value
                for child in ast.walk(expression)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value
            }
        return set()

    changed = True
    while changed:
        changed = False
        for node in nodes:
            targets = []
            expression = None
            if isinstance(node, ast.Assign):
                targets = [target for target in node.targets if isinstance(target, ast.Name)]
                expression = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
                expression = node.value
            elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
                targets = [node.target]
                expression = node.iter
            if expression is None:
                continue
            resolved = values(expression)
            for target in targets:
                prior = bindings.setdefault(target.id, set())
                size = len(prior)
                prior.update(resolved)
                changed |= len(prior) != size
    return bindings


def _app_js_assertion_offenders(relative_path, source):
    """Find unallowlisted assertions whose subject is app.js text.

    Provenance is intentionally lexical and deterministic.  It follows direct
    reads, local aliases, slices, and helper functions that return those
    aliases.  Multi-hop or dynamic provenance through arbitrary calls,
    containers, reflection, or runtime values is not claimed by this guard;
    executable Node tests remain the required proof for those paths.
    """
    tree = ast.parse(source, filename=relative_path)
    frontend_path_names = _frontend_path_names(tree)
    runtime_call_names = _verified_runtime_call_names(tree)
    imported_source_names, runtime_module_names = _shared_runtime_source_imports(tree)
    source_factories = _frontend_source_factories(
        tree, frontend_path_names, runtime_call_names,
        imported_source_names, runtime_module_names,
    )
    offenders = []
    scopes = [tree, *(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )]
    function_defs = {
        node.name: node
        for node in scopes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    tainted_parameters = {}
    for caller_scope in scopes:
        caller_nodes = list(_walk_lexical_scope(caller_scope))
        caller_source_names = _frontend_source_provenance(
            caller_nodes, source_factories, frontend_path_names, runtime_call_names,
            imported_source_names, runtime_module_names,
        )
        for call in (
            node for node in caller_nodes
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ):
            function = function_defs.get(call.func.id)
            if function is None:
                continue
            positional_parameters = [
                arg.arg
                for arg in (*function.args.posonlyargs, *function.args.args)
            ]
            named_parameters = {
                *positional_parameters,
                *(arg.arg for arg in function.args.kwonlyargs),
            }
            for index, argument in enumerate(call.args):
                parameter = (
                    positional_parameters[index]
                    if index < len(positional_parameters)
                    else function.args.vararg.arg if function.args.vararg else None
                )
                if parameter is None:
                    continue
                if (
                    _referenced_names(argument) & caller_source_names
                    or _reads_frontend_source(argument, frontend_path_names)
                    or _references_shared_runtime_source(
                        argument, imported_source_names, runtime_module_names
                    )
                    or any(
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id in source_factories
                        for child in ast.walk(argument)
                    )
                ):
                    tainted_parameters.setdefault(function.name, set()).add(
                        parameter
                    )
            for keyword in call.keywords:
                parameter = (
                    keyword.arg
                    if keyword.arg in named_parameters
                    else function.args.kwarg.arg if function.args.kwarg else None
                )
                if parameter is not None and (
                    _referenced_names(keyword.value) & caller_source_names
                    or _reads_frontend_source(keyword.value, frontend_path_names)
                    or _references_shared_runtime_source(
                        keyword.value, imported_source_names, runtime_module_names
                    )
                ):
                    tainted_parameters.setdefault(function.name, set()).add(
                        parameter
                    )
    for scope in scopes:
        scope_nodes = list(_walk_lexical_scope(scope))
        source_names = _frontend_source_provenance(
            scope_nodes, source_factories, frontend_path_names, runtime_call_names,
            {
                *imported_source_names,
                *tainted_parameters.get(getattr(scope, "name", ""), ()),
            },
            runtime_module_names,
        )
        runtime_names = _runtime_output_names(scope_nodes, runtime_call_names)
        string_bindings = _resolved_string_bindings(scope_nodes)
        for node in scope_nodes:
            if not isinstance(node, ast.Assert):
                continue
            test = node.test
            references_source = (
                _referenced_names(test) & source_names
                or _reads_frontend_source(test, frontend_path_names)
                or _references_shared_runtime_source(
                    test, imported_source_names, runtime_module_names
                )
                or any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id in source_factories
                    for child in ast.walk(test)
                )
            )
            if (
                _is_runtime_output_expression(test, runtime_names, runtime_call_names)
                and not references_source
            ):
                continue
            if not references_source:
                continue
            function_name = _function_name_for_line(tree, node.lineno)
            literals = {
                child.value
                for child in ast.walk(test)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
            }
            if not literals:
                # Indirect needles (``needle in APP_JS``, loop variables, and
                # ``re.search(pattern, APP_JS)``) still need a source contract.
                # Resolve only simple local literals; dynamic values remain an
                # explicit offender via a deterministic sentinel.
                referenced = [
                    child.id
                    for child in ast.walk(test)
                    if isinstance(child, ast.Name)
                ]
                resolved = {
                    value
                    for name in referenced
                    for value in string_bindings.get(name, ())
                }
                if not resolved:
                    source_reference = next(
                        (name for name in referenced if name not in source_names),
                        next((name for name in referenced if name in source_names), "<source>"),
                    )
                    resolved = {f"<indirect:{source_reference}>"}
                literals = resolved
            for literal in sorted(literals):
                key = (relative_path, function_name, literal)
                if key not in APP_JS_SOURCE_ASSERTION_ALLOWLIST:
                    offenders.append((relative_path, function_name, node.lineno, literal))
    return offenders


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


def _pytest_test_module_paths(root):
    return sorted({
        *root.rglob("test_*.py"),
        *root.rglob("*_test.py"),
    })


def test_frontend_contracts_reject_unallowlisted_app_js_behavior_assertions():
    offenders = []
    for path in _pytest_test_module_paths(ROOT / "tests"):
        if path.name == Path(__file__).name:
            continue
        relative_path = path.relative_to(ROOT).as_posix()
        offenders.extend(
            _app_js_assertion_offenders(
                relative_path, path.read_text(encoding="utf-8")
            )
        )

    rendered = [
        f"{path}:{function}:{line}: {literal!r}"
        for path, function, line, literal in offenders
    ]
    assert rendered == [], "Unallowlisted app.js source assertions:\n" + "\n".join(rendered)


def test_app_js_guard_discovers_nested_pytest_module_patterns(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    expected = {
        nested / "test_prefix.py",
        nested / "suffix_test.py",
    }
    for path in [*expected, nested / "helper.py"]:
        path.write_text("", encoding="utf-8")

    assert set(_pytest_test_module_paths(tmp_path)) == expected


def test_app_js_guard_catches_direct_alias_membership_and_regex_forms():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            local_alias = APP_JS
            assert 'direct' in APP_JS
            assert 'alias' not in local_alias
            assert local_alias.count('counted') >= 1
            assert re.search(r'multiline\\nvalue', local_alias)
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "direct"),
        ("test_contract", "alias"),
        ("test_contract", "counted"),
        ("test_contract", "multiline\\nvalue"),
    }


def test_app_js_guard_tracks_function_local_app_js_path_variables():
    source = dedent(
        """
        def test_contract():
            app_path = Path('static/js/app.js')
            source = app_path.read_text()
            assert 'function-local behavior' in source
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "function-local behavior"),
    }


def test_app_js_guard_catches_indirect_needle_loop_and_regex_variables():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            needle = 'variable needle'
            assert needle in APP_JS
            for token in ('loop token',):
                assert token in APP_JS
            pattern = r'regex variable'
            assert re.search(pattern, APP_JS)
            dynamic = make_needle()
            assert dynamic in APP_JS
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "variable needle"),
        ("test_contract", "loop token"),
        ("test_contract", "regex variable"),
        ("test_contract", "<indirect:dynamic>"),
    }


def test_app_js_guard_catches_inline_source_factory_calls():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def app_block():
            return APP_JS
        def test_contract():
            assert 'inline factory behavior' in app_block()
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "inline factory behavior"),
    }


def test_app_js_guard_propagates_source_into_assertion_helper_parameters():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def assert_contract(source):
            assert 'helper parameter behavior' in source
        def test_contract():
            assert_contract(APP_JS)
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("assert_contract", "helper parameter behavior"),
    }


def test_app_js_guard_does_not_trust_same_named_local_runtime_helper():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def run_app_js():
            return APP_JS
        def test_contract():
            assert 'local helper behavior' in run_app_js()
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "local helper behavior"),
    }


def test_app_js_guard_does_not_trust_rebound_imported_runtime_helper():
    source = dedent(
        """
        from js_runtime import run_app_js
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            run_app_js = lambda *_args: APP_JS
            output = run_app_js([], '')
            assert 'rebound runtime helper behavior' in output
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "rebound runtime helper behavior"),
    }


def test_shared_runtime_scenario_cannot_read_embedded_app_source_binding():
    with pytest.raises(AssertionError, match="ReferenceError"):
        run_app_js([], "process.stdout.write(JSON.stringify(appSource));")


def test_shared_runtime_scenario_cannot_extract_exported_function_source():
    with pytest.raises(ValueError, match="may not reflect function source"):
        run_app_js(
            ["api"],
            "process.stdout.write(JSON.stringify(e.api.toString()));",
        )


def test_shared_runtime_scenario_cannot_extract_source_through_prototype_constructor():
    with pytest.raises(ValueError, match="may not reflect function source"):
        run_app_js(
            ["api"],
            "process.stdout.write(JSON.stringify(e.api.prototype.constructor.toString()));",
        )


def test_shared_runtime_wrapper_does_not_pass_raw_function_to_mutated_reflect_apply():
    result = run_app_js(
        ["workoutAdaptationIsRenderable"],
        """
let leaked = null;
Reflect.apply = (target) => { leaked = target; return false; };
const renderable = e.workoutAdaptationIsRenderable({
  id: 'event-1', status: 'applied', change_type: 'changed',
});
process.stdout.write(JSON.stringify({ renderable, leaked: leaked !== null }));
""",
    )
    assert result == {"renderable": True, "leaked": False}


def test_shared_runtime_scenario_cannot_reach_unwrapped_exports_through_sandbox():
    with pytest.raises(ValueError, match="may not reflect function source"):
        run_app_js(
            ["api"],
            "process.stdout.write(JSON.stringify(sandbox.__fitExports.api.toString()));",
        )


def test_shared_runtime_scenario_cannot_reach_raw_debug_exports():
    with pytest.raises(ValueError, match="may not reflect function source"):
        run_app_js(
            ["switchTab"],
            """
const source = Function.prototype.toString.call(sandbox.__aicoach.switchTab);
process.stdout.write(JSON.stringify(source));
""",
        )


def test_shared_runtime_blocks_string_coercion_of_callbacks_crossing_mock_boundary():
    with pytest.raises(AssertionError, match="Source reflection is not allowed"):
        run_app_js(
            ["registerServiceWorker"],
            """
let captured = null;
sandbox.navigator.serviceWorker = {
  addEventListener(_type, callback) { captured = callback; },
  register() { return new Promise(() => {}); },
};
e.registerServiceWorker();
process.stdout.write(JSON.stringify(String(captured)));
""",
        )


def test_shared_runtime_blocks_indirect_host_realm_callback_reflection():
    with pytest.raises(AssertionError, match="Source reflection is not allowed"):
        run_app_js(
            ["registerServiceWorker"],
            """
let captured = null;
sandbox.navigator.serviceWorker = {
  addEventListener(_type, callback) { captured = callback; },
  register() { return new Promise(() => {}); },
};
e.registerServiceWorker();
const stringify = URL.constructor.prototype['to' + 'String'];
const source = Reflect.apply(stringify, captured, []);
process.stdout.write(JSON.stringify(source));
""",
        )


def test_shared_runtime_scenario_cannot_import_app_source_from_filesystem():
    with pytest.raises(AssertionError, match="ReferenceError"):
        run_app_js(
            [],
            """
const source = require('node:fs').readFileSync('static/js/app.js', 'utf8');
process.stdout.write(JSON.stringify(source));
""",
        )


def test_shared_runtime_process_permissions_block_host_constructor_escape():
    with pytest.raises(AssertionError, match="ERR_ACCESS_DENIED"):
        run_app_js(
            [],
            """
const hostProcess = URL.constructor.constructor('return process')();
const source = hostProcess.getBuiltinModule('node:fs').readFileSync('static/js/app.js', 'utf8');
process.stdout.write(JSON.stringify(source));
""",
        )


def test_shared_runtime_rejects_export_expressions_that_can_hide_functions():
    with pytest.raises(ValueError, match="binding identifiers"):
        run_app_js(
            ["wrapped: { fn: api }"],
            "process.stdout.write(JSON.stringify(e.wrapped.fn.toString()));",
        )


def test_shared_runtime_rejects_mock_expressions_that_can_leak_functions():
    with pytest.raises(ValueError, match="mocks must be binding identifiers"):
        run_app_js(
            ["api"],
            "process.stdout.write(JSON.stringify(sandbox.__fitSet.leak.toString()));",
            mocks=["leak: api, api"],
        )


@pytest.mark.parametrize(
    "read_expression",
    [
        "app_path.read_bytes().decode()",
        "app_path.open().read()",
        "open(app_path).read()",
    ],
)
def test_app_js_guard_tracks_common_alternative_file_reads(read_expression):
    source = dedent(
        f"""
        def test_contract():
            app_path = Path('static/js/app.js')
            source = {read_expression}
            assert 'alternative read behavior' in source
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "alternative read behavior"),
    }


def test_app_js_guard_rejects_runtime_calls_with_source_arguments():
    source = dedent(
        """
        from js_runtime import run_app_js
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            output = run_app_js([], '')
            assert output.startswith(APP_JS)
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "<indirect:output>"),
    }


def test_app_js_guard_preserves_taint_through_runtime_receiver_methods():
    source = dedent(
        """
        from js_runtime import run_app_js
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            output = run_app_js([], '')
            mixed = output.replace('x', APP_JS)
            assert 'mixed method behavior' in mixed
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "mixed method behavior"),
    }


@pytest.mark.parametrize(
    "import_line, source_expression",
    [
        ("from js_runtime import APP_JS as source", "source"),
        ("import js_runtime as runtime", "runtime.APP_JS"),
    ],
)
def test_app_js_guard_tracks_source_imported_from_shared_runtime(
    import_line, source_expression,
):
    source = dedent(
        f"""
        {import_line}
        def test_contract():
            assert 'imported runtime source behavior' in {source_expression}
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "imported runtime source behavior"),
    }


def test_app_js_guard_propagates_source_through_all_parameter_kinds():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def positional_only(source, /):
            assert 'positional-only behavior' in source
        def keyword_only(*, source):
            assert 'keyword-only behavior' in source
        def variadic_positional(*sources):
            assert 'varargs behavior' in sources[0]
        def variadic_keyword(**sources):
            assert 'kwargs behavior' in sources['source']
        def test_contract():
            positional_only(APP_JS)
            keyword_only(source=APP_JS)
            variadic_positional(APP_JS)
            variadic_keyword(source=APP_JS)
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("positional_only", "positional-only behavior"),
        ("keyword_only", "keyword-only behavior"),
        ("variadic_positional", "varargs behavior"),
        ("variadic_keyword", "kwargs behavior"),
        ("variadic_keyword", "source"),
    }


def test_app_js_guard_does_not_treat_json_roundtrip_as_runtime_output():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            assert 'async function api' in json.loads(json.dumps(APP_JS))
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "async function api"),
    }


def test_app_js_guard_does_not_launder_source_in_mixed_runtime_containers():
    source = dedent(
        """
        from js_runtime import run_app_js
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            output = {'runtime': run_app_js([], ''), 'source': APP_JS}
            assert 'mixed source behavior' in output['source']
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "mixed source behavior"),
        ("test_contract", "source"),
    }


def test_app_js_guard_does_not_trust_arbitrary_subprocess_commands():
    source = dedent(
        """
        import subprocess
        APP_JS = Path('static/js/app.js').read_text()
        def test_contract():
            result = subprocess.run(['printf', '%s', APP_JS], capture_output=True)
            assert 'cat command behavior' in result.stdout
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_contract", "cat command behavior"),
    }


def test_app_js_guard_does_not_trust_direct_node_subprocesses():
    source = dedent(
        """
        APP_JS = Path('static/js/app.js').read_text()
        APP_HTML = Path('templates/index.html').read_text()
        APP_CSS = Path('static/css/style.css').read_text()
        def runtime_fixture():
            source_alias = APP_JS
            result = subprocess.run(['node', '-'], input=source_alias, capture_output=True)
            return json.loads(result.stdout)
        def test_runtime():
            output = runtime_fixture()
            result = subprocess.run(['node', '-'], input=APP_JS, capture_output=True)
            decoded = json.loads(result.stdout)
            assert 'stable' in decoded
            assert output == {'source': 'stable'}
            assert 'markup' in APP_HTML
            assert 'style' not in APP_CSS
        """
    )

    offenders = _app_js_assertion_offenders("tests/synthetic.py", source)

    assert {(entry[1], entry[3]) for entry in offenders} == {
        ("test_runtime", "stable"),
        ("test_runtime", "source"),
    }


def test_app_js_guard_accepts_only_exact_allowlist_tuples():
    literal = "const MEAL_QUEUE_DB_VERSION = 1;"
    source = dedent(
        f"""
        APP_JS = Path('static/js/app.js').read_text()
        def test_meal_queue_uses_versioned_indexeddb_schema():
            assert {literal!r} in APP_JS
        """
    )

    assert _app_js_assertion_offenders(
        "tests/test_fit145_offline_queue.py", source
    ) == []
    assert _app_js_assertion_offenders(
        "tests/other.py", source
    )
