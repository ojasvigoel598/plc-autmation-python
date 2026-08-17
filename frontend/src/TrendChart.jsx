import React from "react";

/**
 * Lightweight dual-axis live trend.  `spec` is an array of
 * { key, label, color, dash, axis } where axis 2 is scaled 0..100 %.
 * Renders crisp HTML labels; the SVG contains only lines/grid so it can
 * stretch responsively without distorting text.
 */
export default function TrendChart({ title, rangeLabel, data, spec, yMin = 0, yMax = 2, height = 160 }) {
  const W = 640;
  const H = height;
  const padL = 10;
  const padR = 10;
  const padT = 8;
  const padB = 8;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const ready = data && Array.isArray(data.t) && data.t.length >= 2;

  const toPoints = (key) => {
    const vals = data.series[key];
    if (!vals || vals.length < 2) return null;
    const t = data.t;
    const t0 = t[0];
    const t1 = t[t.length - 1];
    const span = Math.max(1e-9, t1 - t0);
    const s = spec.find((x) => x.key === key);
    const scale = s && s.scale ? s.scale : 1;
    const lo = s && s.axis === 2 ? 0 : yMin;
    const hi = s && s.axis === 2 ? 100 : yMax;
    let pts = "";
    for (let i = 0; i < vals.length; i++) {
      const px = padL + ((t[i] - t0) / span) * plotW;
      const py = padT + plotH - ((vals[i] * scale - lo) / (hi - lo)) * plotH;
      pts += `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`;
    }
    return pts;
  };

  return (
    <div className="trend-chart">
      <div className="tc-head">
        <span className="tc-title">{title}</span>
        <span className="tc-range">{rangeLabel}</span>
        <span className="tc-legend">
          {spec.map((s) => (
            <span key={s.key} className="tc-item" style={{ color: s.color }}>
              <i style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="tc-svg">
        <rect x={padL} y={padT} width={plotW} height={plotH} fill="#0b0f15" />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <line
            key={f}
            x1={padL}
            x2={padL + plotW}
            y1={padT + plotH * f}
            y2={padT + plotH * f}
            stroke="#1c2430"
            strokeWidth="1"
          />
        ))}
        {ready &&
          spec.map((s) => {
            const pts = toPoints(s.key);
            if (!pts) return null;
            return (
              <path
                key={s.key}
                d={pts}
                fill="none"
                stroke={s.color}
                strokeWidth={s.width || 1.6}
                strokeDasharray={s.dash || undefined}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
      </svg>
    </div>
  );
}
