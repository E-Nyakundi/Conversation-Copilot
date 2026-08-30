"use client";

// The signature element: a segmented meter bar, styled after a hardware
// VU meter — fitting for a product whose entire domain is audio levels.
// Each cell lights up as usage climbs, shifting from accent green to
// amber near the cap to red at/over it, rather than a single smooth
// progress fill. null max renders as an open, fully-dim "unlimited"
// rail with an infinity mark instead of a filled bar.

const CELLS = 12;

export default function UsageMeter({ label, used, max, unit }) {
  if (max === null || max === undefined) {
    return (
      <div className="meter">
        <div className="meter-head">
          <span className="meter-label">{label}</span>
          <span className="meter-value mono">
            {formatNum(used)} <span className="meter-unit">{unit}</span> · unlimited
          </span>
        </div>
        <div className="meter-rail">
          {Array.from({ length: CELLS }).map((_, i) => (
            <span key={i} className="cell cell-open" />
          ))}
        </div>
        <style jsx>{styles}</style>
      </div>
    );
  }

  const ratio = max > 0 ? used / max : used > 0 ? 1 : 0;
  const litCells = Math.min(CELLS, Math.round(ratio * CELLS));
  const state = ratio >= 1 ? "danger" : ratio >= 0.8 ? "warn" : "ok";

  return (
    <div className="meter">
      <div className="meter-head">
        <span className="meter-label">{label}</span>
        <span className={`meter-value mono state-${state}`}>
          {formatNum(used)} / {formatNum(max)} <span className="meter-unit">{unit}</span>
        </span>
      </div>
      <div className="meter-rail">
        {Array.from({ length: CELLS }).map((_, i) => (
          <span
            key={i}
            className={`cell ${i < litCells ? `cell-lit cell-${state}` : "cell-off"}`}
          />
        ))}
      </div>
      <style jsx>{styles}</style>
    </div>
  );
}

function formatNum(n) {
  if (n === null || n === undefined) return "—";
  return Number.isInteger(n) ? n : n.toFixed(1);
}

const styles = `
  .meter {
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-width: 180px;
  }
  .meter-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
  }
  .meter-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-faint);
  }
  .meter-value {
    font-size: 12px;
    color: var(--text-dim);
  }
  .meter-unit {
    color: var(--text-faint);
  }
  .state-ok { color: var(--accent); }
  .state-warn { color: var(--warn); }
  .state-danger { color: var(--danger); }

  .meter-rail {
    display: flex;
    gap: 2px;
  }
  .cell {
    height: 8px;
    flex: 1;
    border-radius: 1px;
    background: var(--line-soft);
  }
  .cell-off {
    background: var(--line-soft);
  }
  .cell-open {
    background: repeating-linear-gradient(
      45deg,
      var(--line-soft),
      var(--line-soft) 3px,
      transparent 3px,
      transparent 6px
    );
  }
  .cell-lit.cell-ok { background: var(--accent-dim); }
  .cell-lit.cell-warn { background: var(--warn); }
  .cell-lit.cell-danger { background: var(--danger); }
`;
