"use client";

import { useEffect, useState } from "react";
import UsageMeter from "./UsageMeter";
import { api, ApiError } from "../../lib/api";

export default function UserRow({ user, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const q = user.quota;

  async function run(action) {
    setErr("");
    setBusy(true);
    try {
      await action();
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  const status = !q?.is_active
    ? "revoked"
    : q?.sessions_remaining === 0 || q?.minutes_remaining === 0
    ? "exhausted"
    : "active";

  return (
    <div className={`row status-${status}`}>
      <div className="row-main">
        <div className="identity">
          <span className={`dot dot-${status}`} />
          <div>
            <div className="email">{user.email}</div>
            <div className="meta mono">
              {user.must_change_password ? "invite pending" : "active"} · joined{" "}
              {new Date(user.created_at).toLocaleDateString()}
            </div>
          </div>
        </div>

        <div className="meters">
          <UsageMeter
            label="Sessions"
            used={q?.sessions_used ?? 0}
            max={q?.max_sessions ?? null}
            unit=""
          />
          <UsageMeter
            label="Minutes"
            used={q?.minutes_used ?? 0}
            max={q?.max_minutes ?? null}
            unit="min"
          />
        </div>

        <div className="actions">
          <button
            className="btn"
            disabled={busy}
            onClick={() => run(() => api.adjustQuota(user.id, { sessions_delta: 5 }))}
            title="Grant 5 more sessions"
          >
            +5 sess
          </button>
          <button
            className="btn"
            disabled={busy}
            onClick={() => run(() => api.adjustQuota(user.id, { minutes_delta: 60 }))}
            title="Grant 60 more minutes"
          >
            +60 min
          </button>
          {q?.is_active ? (
            <button
              className="btn btn-danger"
              disabled={busy}
              onClick={() => run(() => api.revoke(user.id))}
            >
              Revoke
            </button>
          ) : (
            <button
              className="btn btn-accent"
              disabled={busy}
              onClick={() => run(() => api.reactivate(user.id))}
            >
              Reactivate
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Hide" : "Details"}
          </button>
          {confirmingDelete ? (
            <>
              <span className="confirm-label mono">delete permanently?</span>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={() =>
                  run(() => api.deleteUser(user.id)).finally(() => setConfirmingDelete(false))
                }
              >
                Confirm
              </button>
              <button
                className="btn btn-ghost"
                disabled={busy}
                onClick={() => setConfirmingDelete(false)}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              className="btn btn-danger-outline"
              disabled={busy}
              onClick={() => setConfirmingDelete(true)}
              title="Permanently delete this user and all their data"
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {err && <div className="error">{err}</div>}

      {expanded && <UserDetail userId={user.id} />}

      <style jsx>{`
        .row {
          border: 1px solid var(--line);
          border-left: 3px solid var(--line);
          border-radius: 4px;
          background: var(--bg-raised);
          padding: 16px 18px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .status-active { border-left-color: var(--accent-dim); }
        .status-exhausted { border-left-color: var(--warn); }
        .status-revoked { border-left-color: var(--danger); opacity: 0.75; }

        .row-main {
          display: grid;
          grid-template-columns: 1.4fr 1.6fr auto;
          gap: 20px;
          align-items: center;
        }
        .identity {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
        }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          flex-shrink: 0;
        }
        .dot-active { background: var(--accent); box-shadow: 0 0 6px var(--accent-dim); }
        .dot-exhausted { background: var(--warn); }
        .dot-revoked { background: var(--danger); }

        .email {
          font-weight: 600;
          font-size: 14px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .meta {
          font-size: 11px;
          color: var(--text-faint);
          margin-top: 2px;
        }
        .meters {
          display: flex;
          gap: 24px;
        }
        .actions {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
          justify-content: flex-end;
        }
        .actions .btn {
          padding: 6px 10px;
          font-size: 12px;
          white-space: nowrap;
        }
        .error {
          font-size: 12px;
          color: var(--danger);
        }
        .confirm-label {
          font-size: 11px;
          color: var(--danger);
          align-self: center;
          white-space: nowrap;
        }
        .btn-danger-outline {
          background: transparent;
          border: 1px solid var(--danger);
          color: var(--danger);
        }
        .btn-danger-outline:hover:not(:disabled) {
          background: var(--danger);
          color: var(--bg-raised);
        }

        @media (max-width: 860px) {
          .row-main {
            grid-template-columns: 1fr;
            align-items: stretch;
          }
          .actions {
            justify-content: flex-start;
          }
        }
      `}</style>
    </div>
  );
}

function UserDetail({ userId }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .userDetail(userId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (loading) return <div className="detail mono loading">loading history…</div>;
  if (!detail) return null;

  return (
    <div className="detail">
      <div className="detail-label mono">USAGE LOG</div>
      {detail.usage_events.length === 0 ? (
        <div className="empty">No activity yet.</div>
      ) : (
        <ul>
          {detail.usage_events.map((ev) => (
            <li key={ev.id} className="mono">
              <span className="ts">{new Date(ev.created_at).toLocaleString()}</span>
              {ev.session_id ? (
                <span> · session ended · {ev.minutes.toFixed(1)}min</span>
              ) : (
                <span className="grant"> · {ev.note}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <style jsx>{`
        .detail {
          border-top: 1px solid var(--line-soft);
          padding-top: 12px;
          margin-top: 4px;
        }
        .detail-label {
          font-size: 10px;
          letter-spacing: 0.1em;
          color: var(--text-faint);
          margin-bottom: 8px;
        }
        .loading, .empty {
          font-size: 12px;
          color: var(--text-faint);
        }
        ul {
          list-style: none;
          margin: 0;
          padding: 0;
          display: flex;
          flex-direction: column;
          gap: 4px;
          max-height: 180px;
          overflow-y: auto;
        }
        li {
          font-size: 12px;
          color: var(--text-dim);
        }
        .ts {
          color: var(--text-faint);
        }
        .grant {
          color: var(--accent-dim);
        }
      `}</style>
    </div>
  );
}
