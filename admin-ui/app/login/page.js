"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken, ApiError } from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await api.login(email, password);
      setToken(res.token);
      if (res.user.must_change_password) {
        router.push("/change-password");
      } else if (!res.user.is_staff) {
        setError("This account isn't authorized for the control room.");
      } else {
        router.push("/");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reach the server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wrap">
      <form className="panel" onSubmit={handleSubmit}>
        <div className="eyebrow mono">CONVERSATION COPILOT</div>
        <h1>Control Room</h1>
        <p className="sub">Owner access only. Invited tenants sign in from the app.</p>

        <label className="field">
          <span>Email</span>
          <input
            type="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="owner@yourdomain.com"
          />
        </label>

        <label className="field">
          <span>Password</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
          />
        </label>

        {error && <div className="error">{error}</div>}

        <button className="btn btn-accent" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>

      <style jsx>{`
        .wrap {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 24px;
        }
        .panel {
          width: 100%;
          max-width: 380px;
          background: var(--bg-raised);
          border: 1px solid var(--line);
          border-radius: 6px;
          padding: 32px;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .eyebrow {
          font-size: 11px;
          letter-spacing: 0.14em;
          color: var(--accent-dim);
        }
        h1 {
          margin: 0;
          font-size: 22px;
          font-weight: 700;
        }
        .sub {
          margin: 0 0 8px;
          color: var(--text-dim);
          font-size: 13px;
          line-height: 1.5;
        }
        .field {
          display: flex;
          flex-direction: column;
          gap: 6px;
          font-size: 13px;
          color: var(--text-dim);
        }
        .field input {
          width: 100%;
        }
        .error {
          font-size: 13px;
          color: var(--danger);
          background: rgba(232, 93, 93, 0.08);
          border: 1px solid rgba(232, 93, 93, 0.3);
          border-radius: 3px;
          padding: 8px 10px;
        }
        button {
          margin-top: 6px;
          padding: 10px 14px;
        }
      `}</style>
    </div>
  );
}
