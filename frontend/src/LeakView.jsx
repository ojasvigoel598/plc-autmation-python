import React, { useEffect, useState } from "react";

const L_PER_S = (m3s) => ((m3s || 0) * 1000).toFixed(2);

function fmtT(t) {
  if (t == null) return "—";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `T+${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function fmtWall(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleTimeString();
}

function duration(ev) {
  if (ev.status !== "RESOLVED" || ev.t_end == null) return "ongoing";
  const s = ev.t_end - ev.t_start;
  return `${s.toFixed(1)} s`;
}

/* Polls the persistent leak store (REST).  This page is separate from the
   live dashboard; a modest poll keeps "latest" current after refresh. */
function useLeakData() {
  const [data, setData] = useState({ count: 0, events: [], latest: null });
  const [error, setError] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [listR, latestR] = await Promise.all([
          fetch("/api/leaks"),
          fetch("/api/leaks/latest"),
        ]);
        const list = await listR.json();
        const latest = await latestR.json();
        if (alive) {
          setData({ count: list.count, events: list.events, latest: latest.event });
          setError(null);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    load();
    const id = setInterval(load, 3000);
    return () => { alive = false; clearInterval(id); };
  }, []);
  return { data, error };
}

function Field({ label, value, note, tone }) {
  return (
    <div className="ins-row">
      <span className="k">{label}</span>
      <span className={`v ${tone || ""}`}>{value ?? "—"}</span>
      {note && <span className="src">{note}</span>}
    </div>
  );
}

function LeakCard({ ev, prominent }) {
  return (
    <div className={`leak-card ${prominent ? "prominent" : ""} sev-${ev.severity}`}>
      <div className="leak-card-head">
        <span className="leak-id">{ev.id}</span>
        <span className={`badge ${ev.status}`}>{ev.status}</span>
        <span className={`sev sev-${ev.severity}`}>{ev.severity}</span>
      </div>
      <div className="leak-grid">
        <Field label="Equipment" value={ev.tank} />
        <Field label="Location" value={ev.location} />
        <Field label="Leak rate" value={`${L_PER_S(ev.rate_m3s)} L/s`} note="estimated (mass-balance)" />
        <Field label="Level at detection" value={ev.level_before != null ? `${ev.level_before.toFixed(3)} m` : "—"} note="measured" />
        <Field label="Detected" value={fmtT(ev.t_start)} />
        <Field label="Resolved" value={ev.status === "RESOLVED" ? fmtT(ev.t_end) : "—"} />
        <Field label="Duration" value={duration(ev)} />
        <Field label="Wall clock" value={fmtWall(ev.wall_start)} note="recorded at" />
        <Field label="Source" value={ev.source} note="detection method" />
        <Field label="Alarm" value={ev.alarm_tag || "—"} />
      </div>
    </div>
  );
}

function LeakTable({ events, onOpen }) {
  if (!events.length) {
    return <div className="tc-empty">No leak events recorded.</div>;
  }
  return (
    <table className="alarm leak-table">
      <thead>
        <tr>
          <th>ID</th><th>Detected</th><th>Equipment</th><th>Rate</th>
          <th>Severity</th><th>Status</th><th>Duration</th><th></th>
        </tr>
      </thead>
      <tbody>
        {events.map((ev) => (
          <tr key={ev.id} onClick={() => onOpen(ev.id)} className="leak-row">
            <td className="leak-id">{ev.id}</td>
            <td>{fmtT(ev.t_start)}</td>
            <td>{ev.tank}</td>
            <td>{L_PER_S(ev.rate_m3s)} L/s</td>
            <td><span className={`sev sev-${ev.severity}`}>{ev.severity}</span></td>
            <td><span className={`badge ${ev.status}`}>{ev.status}</span></td>
            <td>{duration(ev)}</td>
            <td><button className="ack" onClick={(e) => { e.stopPropagation(); onOpen(ev.id); }}>open</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function LeakView({ route, navigate }) {
  const { data, error } = useLeakData();
  const [detail, setDetail] = useState(null);  // event for /leaks/{id}

  const parts = route.replace(/^\/+/, "").split("/"); // ["leaks"] | ["leaks","latest"] | ["leaks","<id>"]
  const mode = parts[1] === "latest" ? "latest" : parts[1] ? "detail" : "list";

  useEffect(() => {
    if (mode !== "detail") { setDetail(null); return; }
    let alive = true;
    fetch(`/api/leaks/${encodeURIComponent(parts[1])}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setDetail(d); })
      .catch(() => { if (alive) setDetail(null); });
    return () => { alive = false; };
  }, [mode, parts[1]]);

  const latest = mode === "latest" ? data.latest : null;
  const showDetail = latest || (mode === "detail" && detail);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">💧</span>
          <div>
            <h1>Leak / Fault Investigation</h1>
            <p className="sub">persistent event log · mass-balance detection · {data.count} total events</p>
          </div>
        </div>
        <button className="btn" onClick={() => navigate("/")}>← Back to plant</button>
        <button className="btn" onClick={() => navigate("/leaks")}>History</button>
        <button className="btn" onClick={() => navigate("/leaks/latest")}>Latest</button>
      </header>

      <div className="leak-page">
        {error && <div className="override red">Backend unavailable: {error}</div>}

        {mode === "list" && (
          <>
            <div className="panel">
              <h3>Latest leak</h3>
              {data.latest ? (
                <LeakCard ev={data.latest} prominent />
              ) : (
                <div className="tc-empty">No leak events recorded.</div>
              )}
            </div>
            <div className="panel">
              <h3>Previous {Math.min(30, data.events.length)} leak events</h3>
              <LeakTable events={data.events} onOpen={(id) => navigate(`/leaks/${id}`)} />
            </div>
          </>
        )}

        {(mode === "latest" || mode === "detail") && (
          <div className="panel">
            <h3>{mode === "latest" ? "Latest recorded leak" : `Leak event ${parts[1]}`}</h3>
            {showDetail ? (
              <LeakCard ev={showDetail} prominent />
            ) : (
              <div className="tc-empty">
                {mode === "detail" ? "Leak event not found." : "No leak events recorded."}
              </div>
            )}
            <div className="leak-facts">
              <strong>Measured / simulated fact</strong> vs <strong>derived estimate</strong> are labelled per field.
              Leak rate is a mass-balance estimate; level, tank and times are recorded facts.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
