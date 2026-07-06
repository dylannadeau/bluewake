"""Screening report generation (Phase 2) — the actual product.

A report is generated once per screening and stored immutably:
  * JSON: full payload (vessel profile, ruleset evaluated, per-rule findings
    with evidence, data-source as-of timestamps) — written to screenings.report_json
    and hashed into report_sha256 for tamper evidence.
  * PDF: rendered from the JSON via a Jinja2 → WeasyPrint template, stored on
    disk with the path recorded on the screening row.

Every report carries the decision-support disclaimer: findings indicate
red-flag behavior patterns from third-party data and require human review;
absence of findings is not clearance.
"""
