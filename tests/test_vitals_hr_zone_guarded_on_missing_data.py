from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]


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

    # Executable render behavior lives in
    # test_fit264_frontend_runtime::test_vitals_render_clears_stale_hr_zone_when_heart_rate_is_missing.
