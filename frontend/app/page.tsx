"use client";

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type VesselProfile = {
  name: string | null;
  callsign: string | null;
  flag: string | null;
  mmsis: string[];
  flags: string[];
  owners: { name: string; role: string; country: string | null }[];
  last_seen_at: string | null;
};

type ScreeningSummary = { id: string; screened_at: string; overall_status: string };

type Vessel = {
  imo: string;
  profile: VesselProfile;
  active_sdn_matches: number;
  gfw_events: number;
  screenings: ScreeningSummary[];
};

type Finding = { rule_id: string; severity: string; summary: string };

type ScreeningResult = {
  id: string;
  overall_status: string;
  screened_at: string;
  report_sha256: string;
  report: { findings: Finding[] };
};

export default function Home() {
  const [imo, setImo] = useState("");
  const [vessel, setVessel] = useState<Vessel | null>(null);
  const [result, setResult] = useState<ScreeningResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function lookup(target?: string) {
    const q = (target ?? imo).trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${API}/vessels/${q}`);
      if (res.status === 404) {
        setVessel(null);
        setNotice("Vessel not yet in the database — run a screening to create its record.");
      } else if (!res.ok) {
        setVessel(null);
        setError((await res.json()).detail ?? `lookup failed (${res.status})`);
      } else {
        setVessel(await res.json());
      }
    } catch {
      setError("API unreachable");
    } finally {
      setBusy(false);
    }
  }

  async function screen() {
    const q = imo.trim();
    if (!q) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`${API}/vessels/${q}/screenings`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ requested_by: "web-ui" }),
      });
      if (!res.ok) {
        setError((await res.json()).detail ?? `screening failed (${res.status})`);
      } else {
        setResult(await res.json());
        await lookup(q);
      }
    } catch {
      setError("API unreachable");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form
        className="search"
        onSubmit={(e) => {
          e.preventDefault();
          setResult(null); // manual lookup starts fresh; screen() keeps its result
          lookup();
        }}
      >
        <input
          value={imo}
          onChange={(e) => setImo(e.target.value)}
          placeholder="IMO number (7 digits), e.g. 9074729"
          inputMode="numeric"
          maxLength={7}
        />
        <button type="submit" disabled={busy || imo.trim().length !== 7}>
          Look up
        </button>
        <button
          type="button"
          className="secondary"
          onClick={screen}
          disabled={busy || imo.trim().length !== 7}
        >
          Run screening
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {notice && <p className="notice card">{notice}</p>}

      {result && (
        <div className="card">
          <h2>
            Screening result{" "}
            <span className={`badge ${result.overall_status}`}>
              {result.overall_status.replace("_", " ")}
            </span>
          </h2>
          {result.report.findings.length > 0 ? (
            <table>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Rule</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {result.report.findings.map((f, i) => (
                  <tr key={i}>
                    <td className={`sev ${f.severity}`}>{f.severity}</td>
                    <td>{f.rule_id}</td>
                    <td>{f.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="notice">
              No red-flag behaviors detected against the evaluated rules and available data.
            </p>
          )}
          <p className="links" style={{ marginTop: 12 }}>
            <a href={`${API}/screenings/${result.id}/pdf`} target="_blank" rel="noreferrer">
              Download PDF report
            </a>
            <a href={`${API}/screenings/${result.id}`} target="_blank" rel="noreferrer">
              JSON record
            </a>
          </p>
        </div>
      )}

      {vessel && (
        <>
          <div className="card">
            <h2>
              {vessel.profile.name ?? "Unnamed vessel"} — IMO {vessel.imo}
            </h2>
            <dl className="kv">
              <dt>Flag</dt>
              <dd>{vessel.profile.flag ?? "—"}</dd>
              <dt>Call sign</dt>
              <dd>{vessel.profile.callsign ?? "—"}</dd>
              <dt>Known MMSIs</dt>
              <dd>{vessel.profile.mmsis.join(", ") || "—"}</dd>
              <dt>Flag history</dt>
              <dd>{vessel.profile.flags.join(" → ") || "—"}</dd>
              <dt>Recorded owners</dt>
              <dd>
                {vessel.profile.owners.length
                  ? vessel.profile.owners.map((o) => `${o.name} (${o.role})`).join("; ")
                  : "—"}
              </dd>
              <dt>Active SDN matches</dt>
              <dd>{vessel.active_sdn_matches}</dd>
              <dt>GFW events on file</dt>
              <dd>{vessel.gfw_events}</dd>
              <dt>Last seen</dt>
              <dd>{vessel.profile.last_seen_at ?? "—"}</dd>
            </dl>
          </div>

          <div className="card">
            <h2>Screening history</h2>
            {vessel.screenings.length ? (
              <table>
                <thead>
                  <tr>
                    <th>Screened at (UTC)</th>
                    <th>Status</th>
                    <th>Report</th>
                  </tr>
                </thead>
                <tbody>
                  {vessel.screenings.map((s) => (
                    <tr key={s.id}>
                      <td>{s.screened_at.replace("T", " ").slice(0, 19)}</td>
                      <td>
                        <span className={`badge ${s.overall_status}`}>
                          {s.overall_status.replace("_", " ")}
                        </span>
                      </td>
                      <td className="links">
                        <a href={`${API}/screenings/${s.id}/pdf`} target="_blank" rel="noreferrer">
                          PDF
                        </a>
                        <a href={`${API}/screenings/${s.id}`} target="_blank" rel="noreferrer">
                          JSON
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="notice">No screenings yet.</p>
            )}
          </div>
        </>
      )}
    </>
  );
}
