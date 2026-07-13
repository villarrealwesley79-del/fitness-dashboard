from pathlib import Path


def test_native_helper_scope_does_not_claim_unsupported_body_mass_parity():
    sla = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "APPLE_HEALTH_HELPER_SLA.md"
    ).read_text()

    assert "body-mass data already used by the dashboard" not in sla
    assert "Body-mass ingestion is not part of the current bridge contract" in sla


def test_current_state_prd_records_body_mass_scope_decision():
    prd = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "prd"
        / "08-apple-health-integration.md"
    ).read_text()

    assert "Body mass is mentioned in the native helper SLA" not in prd
    assert "The helper SLA says a future native helper may read body-mass data" not in prd
    assert "Body mass remains outside the current Apple Health bridge contract" in prd
    assert "Resolved by FIT-326" in prd
