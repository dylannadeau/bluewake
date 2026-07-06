from app.alerts import render_alert, should_alert
from app.models import Screening, WatchlistEntry


def test_first_screening_alerts_unless_clean():
    assert should_alert(None, "findings")
    assert should_alert(None, "review_required")
    assert not should_alert(None, "no_findings")


def test_alert_on_any_status_change():
    assert should_alert("no_findings", "findings")
    assert should_alert("findings", "no_findings")  # clearing is also news
    assert should_alert("review_required", "findings")


def test_quiet_on_repeat_result():
    assert not should_alert("findings", "findings")
    assert not should_alert("no_findings", "no_findings")


def test_render_alert_email():
    entry = WatchlistEntry(id=1, imo="9321483", label="charter due diligence",
                           alert_email="ops@example.com")
    screening = Screening(
        imo="9321483", overall_status="findings",
        report_json={
            "screened_at": "2026-07-06T12:00:00+00:00",
            "vessel_profile": {"name": "EXAMPLE GLORY"},
            "findings": [
                {"severity": "alert", "rule_id": "dark_sts", "summary": "encounter near gap"}
            ],
        },
    )
    msg = render_alert(entry, screening, previous_status="no_findings")
    assert msg["To"] == "ops@example.com"
    assert "IMO 9321483" in msg["Subject"] and "FINDINGS" in msg["Subject"]
    body = msg.get_content()
    assert "was: no_findings" in body
    assert "[ALERT] dark_sts" in body
    assert "decision-support" in body
