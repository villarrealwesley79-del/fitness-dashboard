"""FIT-152: lineChart non-negative Y-axis contract.

Two assertions:
1. lineChart honours opts.nonNegativeY — the function body must clamp the
   y-domain floor to 0 (Math.max(0, ...)) when the flag is set, so ordinary
   non-negative volume sequences such as [0, 100, 200] never produce a
   negative axis label.
2. lineChart gives zero-only non-negative series a nominal positive max so
   empty volume ranges never render a synthetic 0-to-0.1 lb axis.
3. The History "Volume Over Time" call site passes nonNegativeY: true and
   emptyMaxY: 100 so both guards are active for the chart that was showing
   negative/sub-pound labels.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _linechart_body() -> str:
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "function lineChart(container, points, opts = {})"
    assert marker in app_js, "lineChart not found in app.js"
    return app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]


def test_linechart_honours_nonnegative_y_option():
    """lineChart must clamp minPad to 0 when opts.nonNegativeY is truthy.

    Without this guard, padding (min - range * 0.12) goes negative for any
    series whose minimum value is 0, including the common [0, 100, 200]
    volume pattern, producing impossible negative Y-axis tick labels.
    """
    body = _linechart_body()
    assert "opts.nonNegativeY" in body, (
        "lineChart must read opts.nonNegativeY to know when to apply the "
        "non-negative floor; the flag was not found in the function body"
    )
    assert "Math.max(0," in body, (
        "lineChart must call Math.max(0, ...) to clamp minPad when "
        "opts.nonNegativeY is set — negative y-domain floor causes impossible "
        "axis labels for any series that starts at 0"
    )


def test_linechart_uses_nominal_empty_domain_for_zero_volume_series():
    """Zero-only non-negative charts must use a sane nominal max.

    Without this guard, all-zero volume buckets still render enough lineChart
    points to produce a synthetic 0-to-0.1 lb axis. FIT-152 allows a nominal
    non-negative scale such as 0 to 100 lb for that empty range.
    """
    body = _linechart_body()
    assert "const useEmptyDomain = opts.nonNegativeY && max <= 0;" in body
    assert "const maxPad = useEmptyDomain ? emptyMaxY : max + range * 0.12;" in body


def test_history_volume_chart_passes_nonnegative_y_options():
    """The History 'Volume Over Time' lineChart call must pass non-negative opts.

    Volume is always >= 0 so the y-axis floor must never be negative.
    Empty volume ranges also need a nominal max so the empty chart does not
    show a synthetic sub-pound axis. Passing the opts at this call site (and
    only this call site) avoids changing lineChart defaults for other callers.
    """
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    linechart_call = "lineChart($('chart-history-volume')"
    assert linechart_call in app_js, "lineChart call for chart-history-volume not found"
    volume_call = app_js.split(linechart_call, 1)[1].split("\n", 1)[0]
    assert "nonNegativeY: true" in volume_call, (
        "The lineChart call for chart-history-volume must pass "
        "nonNegativeY: true so the y-axis floor is clamped to 0; "
        f"got: {volume_call!r}"
    )
    assert "emptyMaxY: 100" in volume_call, (
        "The lineChart call for chart-history-volume must pass "
        "emptyMaxY: 100 so empty ranges render a sane 0-to-100 lb scale; "
        f"got: {volume_call!r}"
    )
