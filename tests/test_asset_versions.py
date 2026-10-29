from pathlib import Path


APP_LOADER_JS = Path("static/js/app-loader.js").read_text()
APP_SW_JS = Path("static/js/sw.js").read_text()
APP_HTML = Path("templates/index.html").read_text()

ASSET_VERSION = "20261029-w6"


def test_fit270_asset_versions_are_coordinated():
    assert f"app-loader.js?v={ASSET_VERSION}" in APP_HTML
    assert f"app.js?v={ASSET_VERSION}" in APP_LOADER_JS
    assert f"fitness-dashboard-v{ASSET_VERSION}" in APP_SW_JS
