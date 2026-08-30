"use client";

import { useState } from "react";
import { api, ApiError } from "../../lib/api";

export default function InviteModal({ onClose, onCreated }) {
  const [email, setEmail] = useState("");
  const [maxSessions, setMaxSessions] = useState("10");
  const [maxMinutes, setMaxMinutes] = useState("120");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await api.inviteUser(
        email,
        maxSessions === "" ? null : parseInt(maxSessions, 10),
        maxMinutes === "" ? null : parseInt(maxMinutes, 10)
      );
      setResult(res);
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        {result ? (
          <>
            <div className="eyebrow mono">ACCOUNT CREATED</div>
            <h2>{result.user.email}</h2>
            <p className="sub">
              Share this temporary password now — it won't be shown again. They'll be
              required to set their own on first sign-in.
            </p>
            <div className="secret mono">{result.temp_password}</div>
            <button className="btn btn-accent" onClick={onClose}>
              Done
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <div className="eyebrow mono">NEW TENANT</div>
            <h2>Invite a user</h2>

            <label className="field">
              <span>Email</span>
              <input
                type="email"
                required
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@example.com"
              />
            </label>

            <div className="row">
              <label className="field">
                <span>Session cap</span>
                <input
                  type="number"
                  min="0"
                  value={maxSessions}
                  onChange={(e) => setMaxSessions(e.target.value)}
                  placeholder="blank = unlimited"
                />
              </label>
              <label className="field">
                <span>Minute cap</span>
                <input
                  type="number"
                  min="0"
                  value={maxMinutes}
                  onChange={(e) => setMaxMinutes(e.target.value)}
                  placeholder="blank = unlimited"
                />
              </label>
            </div>

            {error && <div className="error">{error}</div>}

            <div className="actions">
              <button type="button" className="btn btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn btn-accent" disabled={busy}>
                {busy ? "Creating…" : "Create account"}
              </button>
            </div>
          </form>
        )}
      </div>

      <style jsx>{`
        .overlay {
          position: fixed;
          inset: 0;
          background: rgba(6, 10, 8, 0.72);
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
          z-index: 20;
        }
        .panel {
          width: 100%;
          max-width: 420px;
          background: var(--bg-raised);
          border: 1px solid var(--line);
          border-radius: 6px;
          padding: 28px;
        }
        .eyebrow {
          font-size: 11px;
          letter-spacing: 0.14em;
          color: var(--accent-dim);
          margin-bottom: 6px;
        }
        h2 {
          margin: 0 0 6px;
          font-size: 19px;
        }
        .sub {
          color: var(--text-dim);
          font-size: 13px;
          line-height: 1.5;
          margin: 0 0 16px;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
          color: var(--text-dim);
          margin-bottom: 14px;
        }
        .field input {
          width: 100%;
        }
        .row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 12px;
        }
        .error {
          font-size: 13px;
          color: var(--danger);
          background: rgba(232, 93, 93, 0.08);
          border: 1px solid rgba(232, 93, 93, 0.3);
          border-radius: 3px;
          padding: 8px 10px;
          margin-bottom: 12px;
        }
        .actions {
          display: flex;
          justify-content: flex-end;
          gap: 8px;
          margin-top: 4px;
        }
        .secret {
          font-size: 16px;
          letter-spacing: 0.03em;
          background: var(--bg-inset);
          border: 1px solid var(--accent-dim);
          color: var(--accent);
          border-radius: 4px;
          padding: 12px 14px;
          margin-bottom: 18px;
          text-align: center;
        }
      `}</style>
    </div>
  );
}
