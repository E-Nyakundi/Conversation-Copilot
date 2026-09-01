"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api, getToken, clearToken, ApiError } from "../lib/api";
import UserRow from "./components/UserRow";
import InviteModal from "./components/InviteModal";

export default function DashboardPage() {
  const router = useRouter();
  const [users, setUsers] = useState(null);
  const [error, setError] = useState("");
  const [showInvite, setShowInvite] = useState(false);
  const [ready, setReady] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await api.listUsers();
      setUsers(res.users);
      setError("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        router.push("/login");
        return;
      }
      setError(e instanceof ApiError ? e.message : "Couldn't reach the server.");
    }
  }, [router]);

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    api
      .me()
      .then((me) => {
        if (!me.is_staff) {
          router.push("/login");
          return;
        }
        setReady(true);
        load();
      })
      .catch(() => router.push("/login"));
  }, [router, load]);

  function signOut() {
    clearToken();
    router.push("/login");
  }

  if (!ready) return null;

  const totals = summarize(users);

  return (
    <div className="page">
      <header>
        <div>
          <div className="eyebrow mono">CONVERSATION COPILOT</div>
          <h1>Control Room</h1>
        </div>
        <div className="header-actions">
          <button className="btn btn-ghost" onClick={signOut}>
            Sign out
          </button>
          <button className="btn btn-accent" onClick={() => setShowInvite(true)}>
            + Invite user
          </button>
        </div>
      </header>

      {users && (
        <div className="strip">
          <StripStat label="Tenants" value={totals.count} />
          <StripStat label="Active" value={totals.active} tone="ok" />
          <StripStat label="Revoked" value={totals.revoked} tone="danger" />
          <StripStat label="At cap" value={totals.exhausted} tone="warn" />
        </div>
      )}

      {error && <div className="banner-error mono">{error}</div>}

      <main>
        {users === null && !error && <div className="loading mono">reading roster…</div>}
        {users && users.length === 0 && (
          <div className="empty">
            <p>No tenants yet.</p>
            <p className="sub">Invite one to hand out access — you set their limits up front.</p>
          </div>
        )}
        {users && users.length > 0 && (
          <div className="list">
            {users.map((u) => (
              <UserRow key={u.id} user={u} onChanged={load} />
            ))}
          </div>
        )}
      </main>

      {showInvite && (
        <InviteModal
          onClose={() => setShowInvite(false)}
          onCreated={load}
        />
      )}

      <style jsx>{`
        .page {
          max-width: 980px;
          margin: 0 auto;
          padding: 40px 24px 80px;
        }
        header {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          margin-bottom: 28px;
          gap: 16px;
          flex-wrap: wrap;
        }
        .eyebrow {
          font-size: 11px;
          letter-spacing: 0.14em;
          color: var(--accent-dim);
          margin-bottom: 6px;
        }
        h1 {
          margin: 0;
          font-size: 26px;
        }
        .header-actions {
          display: flex;
          gap: 8px;
        }
        .strip {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 1px;
          background: var(--line);
          border: 1px solid var(--line);
          border-radius: 4px;
          overflow: hidden;
          margin-bottom: 28px;
        }
        .banner-error {
          font-size: 13px;
          color: var(--danger);
          background: rgba(232, 93, 93, 0.08);
          border: 1px solid rgba(232, 93, 93, 0.3);
          border-radius: 4px;
          padding: 10px 14px;
          margin-bottom: 20px;
        }
        .loading {
          color: var(--text-faint);
          font-size: 13px;
        }
        .empty {
          border: 1px dashed var(--line);
          border-radius: 6px;
          padding: 40px;
          text-align: center;
          color: var(--text-dim);
        }
        .empty p {
          margin: 0 0 4px;
        }
        .empty .sub {
          font-size: 13px;
          color: var(--text-faint);
        }
        .list {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
      `}</style>
    </div>
  );
}

function StripStat({ label, value, tone }) {
  return (
    <div className={`stat ${tone ? `tone-${tone}` : ""}`}>
      <div className="stat-value mono">{value}</div>
      <div className="stat-label">{label}</div>
      <style jsx>{`
        .stat {
          background: var(--bg-raised);
          padding: 16px 18px;
        }
        .stat-value {
          font-size: 24px;
          font-weight: 600;
          color: var(--text);
        }
        .tone-ok .stat-value { color: var(--accent); }
        .tone-warn .stat-value { color: var(--warn); }
        .tone-danger .stat-value { color: var(--danger); }
        .stat-label {
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: var(--text-faint);
          margin-top: 4px;
        }
      `}</style>
    </div>
  );
}

function summarize(users) {
  if (!users) return { count: 0, active: 0, revoked: 0, exhausted: 0 };
  let active = 0,
    revoked = 0,
    exhausted = 0;
  for (const u of users) {
    const q = u.quota;
    if (!q?.is_active) {
      revoked++;
    } else if (q.sessions_remaining === 0) {
      exhausted++;
    } else {
      active++;
    }
  }
  return { count: users.length, active, revoked, exhausted };
}
