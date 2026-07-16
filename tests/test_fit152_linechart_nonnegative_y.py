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
from js_runtime import run_app_js


def test_linechart_honours_nonnegative_y_option():
    """lineChart must clamp minPad to 0 when opts.nonNegativeY is truthy.

    Without this guard, padding (min - range * 0.12) goes negative for any
    series whose minimum value is 0, including the common [0, 100, 200]
    volume pattern, producing impossible negative Y-axis tick labels.
    """
    labels = run_app_js(
        ["lineChart"],
        """
const makeNode = (tag) => ({
  tag, attrs: {}, children: [], textContent: '',
  setAttribute(key, value) { this.attrs[key] = String(value); },
  appendChild(child) { this.children.push(child); },
});
sandbox.document.createElementNS = (_ns, tag) => makeNode(tag);
const container = makeNode('div');
container.innerHTML = '';
e.lineChart(container, [
  { value: 0, label: 'A' },
  { value: 100, label: 'B' },
  { value: 200, label: 'C' },
], { nonNegativeY: true });
const texts = [];
const visit = (node) => {
  if (node.tag === 'text') texts.push(node.textContent);
  (node.children || []).forEach(visit);
};
container.children.forEach(visit);
process.stdout.write(JSON.stringify(texts));
""",
    )

    numeric_labels = [float(label) for label in labels if label not in {"A", "B", "C"}]
    assert min(numeric_labels) == 0
    assert all(label >= 0 for label in numeric_labels)


def test_linechart_uses_nominal_empty_domain_for_zero_volume_series():
    """Zero-only non-negative charts must use a sane nominal max.

    Without this guard, all-zero volume buckets still render enough lineChart
    points to produce a synthetic 0-to-0.1 lb axis. FIT-152 allows a nominal
    non-negative scale such as 0 to 100 lb for that empty range.
    """
    labels = run_app_js(
        ["lineChart"],
        """
const makeNode = (tag) => ({
  tag, attrs: {}, children: [], textContent: '',
  setAttribute(key, value) { this.attrs[key] = String(value); },
  appendChild(child) { this.children.push(child); },
});
sandbox.document.createElementNS = (_ns, tag) => makeNode(tag);
const container = makeNode('div');
container.innerHTML = '';
e.lineChart(container, [
  { value: 0, label: 'A' },
  { value: 0, label: 'B' },
], { nonNegativeY: true, emptyMaxY: 100 });
const texts = [];
const visit = (node) => {
  if (node.tag === 'text') texts.push(node.textContent);
  (node.children || []).forEach(visit);
};
container.children.forEach(visit);
process.stdout.write(JSON.stringify(texts));
""",
    )

    numeric_labels = [float(label) for label in labels if label not in {"A", "B"}]
    assert min(numeric_labels) == 0
    assert max(numeric_labels) == 100


def test_history_volume_chart_passes_nonnegative_y_options():
    """The History 'Volume Over Time' lineChart call must pass non-negative opts.

    Volume is always >= 0 so the y-axis floor must never be negative.
    Empty volume ranges also need a nominal max so the empty chart does not
    show a synthetic sub-pound axis. Passing the opts at this call site (and
    only this call site) avoids changing lineChart defaults for other callers.
    """
    options = run_app_js(
        ["renderHistory", "state"],
        """
const ids = [
  'history-count', 'history-freq-sub', 'history-total-volume', 'history-vol-sub',
  'chart-history-freq', 'chart-history-volume', 'history-top-exercises',
  'history-workout-list', 'history-type-filter',
];
ids.forEach((id) => {
  sandbox.elements[id] = {
    innerHTML: '', textContent: '', children: [],
    appendChild(child) { this.children.push(child); },
  };
});
let captured;
e.state.ranges = { history: 30 };
sandbox.__fitSet.getHistory(async () => ({ workouts: [] }));
sandbox.__fitSet.getAppleHealthWorkouts(async () => []);
sandbox.__fitSet.barChart(() => {});
sandbox.__fitSet.lineChart((_container, _points, opts) => { captured = opts; });
sandbox.__fitSet.setChartTakeaway(() => {});
await e.renderHistory();
process.stdout.write(JSON.stringify(captured));
""",
        mocks=[
            "getHistory", "getAppleHealthWorkouts", "barChart", "lineChart",
            "setChartTakeaway",
        ],
    )

    assert options["nonNegativeY"] is True
    assert options["emptyMaxY"] == 100
