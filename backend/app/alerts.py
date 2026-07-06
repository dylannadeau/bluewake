"""Email alerts for watchlist re-screenings.

Plain SMTP (no provider SDK) so any transactional service or self-hosted
relay drops in via config. If SMTP is unconfigured the alert row is still
recorded with status 'skipped_no_smtp' — the audit trail never depends on
delivery working.
"""

import asyncio
import smtplib
from email.message import EmailMessage

from app.config import settings
from app.models import Screening, WatchlistEntry


def render_alert(entry: WatchlistEntry, screening: Screening, previous_status: str | None) -> EmailMessage:
    report = screening.report_json
    findings = report.get("findings", [])
    lines = [
        f"Vessel: IMO {screening.imo} ({report.get('vessel_profile', {}).get('name') or 'name unknown'})",
        f"Watchlist label: {entry.label or '—'}",
        f"Screening: {screening.id}",
        f"Screened at: {report.get('screened_at')}",
        f"Status: {screening.overall_status}" + (f" (was: {previous_status})" if previous_status else ""),
        "",
    ]
    if findings:
        lines.append("Findings:")
        lines += [f"  [{f['severity'].upper()}] {f['rule_id']}: {f['summary']}" for f in findings]
    else:
        lines.append("No findings against the evaluated rules and available data.")
    lines += [
        "",
        "This is a decision-support notification, not a legal determination. "
        "Review the full report before acting.",
    ]

    msg = EmailMessage()
    msg["From"] = settings.alert_from_address
    msg["To"] = entry.alert_email
    msg["Subject"] = (
        f"[bluewake] IMO {screening.imo} screening: "
        f"{screening.overall_status.replace('_', ' ').upper()}"
    )
    msg.set_content("\n".join(lines))
    return msg


def _send_sync(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_alert(entry: WatchlistEntry, screening: Screening, previous_status: str | None) -> str:
    """Send the alert email; returns the resulting alert status string."""
    if not settings.smtp_host:
        return "skipped_no_smtp"
    msg = render_alert(entry, screening, previous_status)
    try:
        await asyncio.to_thread(_send_sync, msg)
        return "sent"
    except Exception:
        return "failed"


def should_alert(previous_status: str | None, new_status: str) -> bool:
    """Alert on any status change, and on the first screening unless clean.
    Repeated identical results stay quiet — the watchlist is a tripwire, not
    a newsletter."""
    if previous_status is None:
        return new_status != "no_findings"
    return new_status != previous_status
