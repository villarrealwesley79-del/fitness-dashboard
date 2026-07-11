"""FIT-263 frontend regression contracts."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")


def _function_block(start_marker: str, end_marker: str) -> str:
    start = APP_JS.index(start_marker)
    end = APP_JS.index(end_marker, start)
    return APP_JS[start:end]


def test_nutrition_hash_targets_existing_log_tab_and_unknown_tabs_fall_back():
    hash_block = _function_block("function initialTabFromHash()", "function switchTabFromHash")
    switch_block = _function_block("function switchTab(tabId)", "function initialTabFromHash")

    assert "if (hash === '#nutrition') return 'tab-log';" in hash_block
    assert "panel.classList.contains('tab-content') ? tabId : 'tab-dashboard'" in switch_block


def test_non_panel_dom_id_falls_back_to_dashboard_at_runtime():
    if not shutil.which("node"):
        pytest.skip("FIT-263 tab runtime contract requires Node.js")

    switch_block = _function_block("function switchTab(tabId)", "function initialTabFromHash")
    script = f"""
const state = {{ currentTab: null }};
const panels = [
  {{ id: 'tab-dashboard', classList: {{ toggle() {{}} }}, setAttribute() {{}} }},
  {{ id: 'tab-log', classList: {{ toggle() {{}} }}, setAttribute() {{}} }},
];
const existing = {{
  'tab-dashboard': {{ classList: {{ contains: (name) => name === 'tab-content' }} }},
  'tab-log': {{ classList: {{ contains: (name) => name === 'tab-content' }} }},
  'btn-ai-status': {{ classList: {{ contains: () => false }} }},
}};
const document = {{ getElementById: (id) => existing[id] || null }};
const qsa = (selector) => selector === '.tab-content' ? panels : [];
const loadTab = () => {{}};
const window = {{ scrollTo() {{}} }};
{switch_block}
switchTab('btn-ai-status');
process.stdout.write(JSON.stringify({{ currentTab: state.currentTab }}));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"currentTab": "tab-dashboard"}


def test_barcode_scan_loop_reuses_cached_dom_elements():
    frame_block = _function_block("async function scanMealBarcodeFrame", "function openMealBarcodePanel")

    assert "mealComposerEls()" not in frame_block
    assert "mealComposerState.barcodeVideo" in frame_block
    assert "mealComposerState.barcodeInput" in frame_block


def test_ai_health_poll_skips_hidden_documents():
    status_block = _function_block("async function refreshAiStatus()", "async function toggleAiPopover")
    boot_block = _function_block("function boot()", "if (document.readyState")

    assert "if (document.visibilityState === 'hidden') return;" in status_block
    assert "else refreshAiStatus();" in boot_block


def test_production_bundle_excludes_fit134_mock_backend():
    assert "mealV2Mock" not in APP_JS
    assert "fit134" not in APP_JS
