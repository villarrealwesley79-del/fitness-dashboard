from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _vitals_body() -> str:
    """Extract the body of `async function renderVitals()` from app.js.

    Other Vitals helpers in this file (`v-rhr-delta`, `v-hrv-delta`, etc.)
    coexist on the same page, and earlier dashboard fetchers also touch
    DOM ids that begin with `v-`. Scoping assertions to renderVitals
    avoids a whole-file grep that could pass on the wrong function.
    """
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function renderVitals()"
    assert marker in app_js, "renderVitals() not found in static/js/app.js"
    return app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]


def test_vitals_hr_zone_guarded_on_missing_data():
    """FIT-149: the HR Zone card must not paint a hard-coded `Fat burn`
    subtitle next to an empty `--` HR value for a user with no wearable /
    current-HR data.

    The fix is twofold and both halves must hold:

    1. The template default for `#v-hr-zone-sub` is empty (not `Fat burn`),
       so the cold-paint cannot flash the placeholder before JS runs.
    2. `renderVitals` explicitly writes `#v-hr-zone-sub` on every render -
       both when HR is present and when it is null - so a stale subtitle
       from a previous session (or an earlier render that did have HR) is
       cleared when backing HR data nulls out.

    There is no repo-backed mapping from HR/RHR to a zone-description
    string, so by design both branches clear the element rather than
    inventing a label.
    """
    template = (ROOT / "templates" / "index.html").read_text()

    # --- Template guard --------------------------------------------------
    # The element must exist (so renderVitals' write target is real) and
    # its initial text must NOT be `Fat burn`.
    assert 'id="v-hr-zone-sub"' in template, (
        "#v-hr-zone-sub element missing from the Vitals template"
    )
    start = template.index('id="v-hr-zone-sub"')
    tag_open = template.rfind("<", 0, start)
    tag_close = template.index(">", start)
    element_end = template.index("</div>", tag_close)
    element_text = template[tag_close + 1:element_end]
    assert "Fat burn" not in element_text, (
        "#v-hr-zone-sub template default must not be 'Fat burn' - that "
        "static subtitle paints next to the '--' value for a fresh user "
        "with no wearable data (FIT-149)"
    )
    # The default must be empty so the cold paint shows nothing under
    # `--`, not stale or invented copy.
    assert element_text.strip() == "", (
        f"#v-hr-zone-sub template default must be empty, got "
        f"{element_text!r}"
    )

    # --- renderVitals runtime guard --------------------------------------
    body = _vitals_body()

    # renderVitals must explicitly write `#v-hr-zone-sub`, otherwise a
    # subtitle that some earlier render painted (or future code paints)
    # would survive across a render where HR data has nulled out.
    assert "$('v-hr-zone-sub').textContent" in body, (
        "renderVitals must explicitly write `#v-hr-zone-sub` so stale "
        "prior-render subtitle text is cleared when HR data is missing "
        "(FIT-149)"
    )
    assert "if (rhr != null) {" in body, (
        "renderVitals must branch on `rhr != null` so missing HR data has "
        "an explicit clear-on-null path instead of relying on a fallback "
        "expression or the template default (FIT-149)"
    )
    assert "} else {\n            $('v-hr-zone').textContent = '--';\n            $('v-hr-zone-sub').textContent = '';" in body, (
        "renderVitals must reset both the HR Zone value and subtitle when "
        "HR data is missing, mirroring FIT-127's clear-on-null pattern"
    )

    # And the write must not reintroduce the `Fat burn` placeholder.
    assert "Fat burn" not in body, (
        "renderVitals must not write the literal 'Fat burn' subtitle - "
        "no repo-backed zone-description mapping exists, so the only "
        "correct write for both HR-present and HR-null is an empty string "
        "(FIT-149)"
    )
