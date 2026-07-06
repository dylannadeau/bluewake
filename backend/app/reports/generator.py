"""Screening report generation — the deliverable customers pay for.

build_report() assembles the canonical JSON payload; its SHA-256 hash is
stored on the screening row for tamper evidence. render_pdf() renders the
same payload to an audit-ready PDF. The JSON is the source of truth; the PDF
is a faithful view of it.
"""

import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

DISCLAIMER = (
    "This report is a decision-support and audit-trail record generated from "
    "third-party data sources (AIS aggregators, OFAC SDN list, Global Fishing "
    "Watch) whose coverage and accuracy vary. Findings indicate red-flag "
    "behavior patterns and possible sanctions exposure; they are not legal "
    "determinations. All findings require review by a qualified compliance "
    "officer before any action is taken. The absence of findings is not "
    "clearance: it reflects only the data available at the time of screening."
)

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def build_report(
    *,
    screening_id: str,
    imo: str,
    screened_at: str,
    trigger: str,
    requested_by: str | None,
    vessel_profile: dict,
    ruleset: list[dict],
    data_sources: dict,
    findings: list[dict],
    overall_status: str,
) -> dict:
    return {
        "report_type": "vessel_sanctions_screening",
        "report_version": "1.0",
        "screening_id": screening_id,
        "imo": imo,
        "screened_at": screened_at,
        "trigger": trigger,
        "requested_by": requested_by,
        "overall_status": overall_status,
        "vessel_profile": vessel_profile,
        "rules_evaluated": ruleset,
        "data_sources": data_sources,
        "findings": findings,
        "disclaimer": DISCLAIMER,
    }


def report_hash(report: dict) -> str:
    """Deterministic SHA-256 over the canonical JSON encoding."""
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_pdf(report: dict, sha256: str) -> bytes:
    # Imported lazily: WeasyPrint needs native pango/cairo libs that test
    # environments may lack; JSON reports must still work there.
    from weasyprint import HTML

    html = _env.get_template("report.html").render(report=report, sha256=sha256)
    return HTML(string=html).write_pdf()
