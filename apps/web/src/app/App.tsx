/**
 * Application shell and routing.
 *
 *   /                     Operations inbox  — overdue orders and exception cases
 *   /orders/:orderId      Order             — lines, shipments, carrier events, activity
 *   /cases/:runId         Case workspace    — findings, citations, tool activity, proposal
 *   /runs                 Investigations    — run history: status, time, cost, errors, feedback
 *   /approvals            Approval inbox    — exact payloads, approve/reject, execution
 *   /knowledge            Knowledge         — upload, version, indexing status, deletion
 *   /metrics              Metrics           — quality, cost, latency and approval outcomes
 *
 * The route table is the product surface. Adding a screen here without a corresponding
 * authorized endpoint is how a UI starts lying about what it can do.
 */

import { useState } from 'react';
import { NavLink, Route, Routes } from 'react-router-dom';

import Approvals from '../features/approvals/Approvals';
import Knowledge from '../features/knowledge/Knowledge';
import PolicyPassage from '../features/knowledge/PolicyPassage';
import Metrics from '../features/metrics/Metrics';
import OrderDetail from '../features/orders/OrderDetail';
import OrdersInbox from '../features/orders/OrdersInbox';
import CaseWorkspace from '../features/runs/CaseWorkspace';
import RunHistory from '../features/runs/RunHistory';
import { Banner } from '../components/Status';
import { useSession } from '../lib/auth';
import { day } from '../lib/format';
import { useSystem } from '../lib/system';

/** The seeded development memberships (scripts/seed_dev_users.py). */
const ACCOUNTS = [
  { email: 'ana.analyst@northwind.example.com', name: 'Ana Analyst', role: 'analyst', org: 'Northwind Supply' },
  { email: 'sam.supervisor@northwind.example.com', name: 'Sam Supervisor', role: 'supervisor', org: 'Northwind Supply' },
  { email: 'hal.analyst@harbor.example.com', name: 'Hal Analyst', role: 'analyst', org: 'Harbor Wholesale' },
  { email: 'hana.supervisor@harbor.example.com', name: 'Hana Supervisor', role: 'supervisor', org: 'Harbor Wholesale' },
];

/**
 * Development sign-in against the local dev issuer (ADR 0008). In staging and the
 * pilot this is replaced by the Auth0 redirect; nothing else in the app changes.
 */
function SignIn() {
  const { signIn, loading, error } = useSession();
  const [email, setEmail] = useState('');

  return (
    <section className="signin card">
      <h1>Sign in to OpsPilot</h1>
      <p className="muted">
        Development issuer: only seeded memberships can sign in. Each organization sees
        only its own orders, investigations and policies.
      </p>
      <div className="accounts">
        {ACCOUNTS.map((account) => (
          <button
            key={account.email}
            type="button"
            className="account"
            disabled={loading}
            onClick={() => void signIn(account.email)}
          >
            <span>
              <strong>{account.name}</strong>
              <small>{account.email}</small>
            </span>
            <span className="badge">
              {account.role} · {account.org}
            </span>
          </button>
        ))}
      </div>
      <form
        className="row"
        onSubmit={(event) => {
          event.preventDefault();
          if (email.trim()) void signIn(email.trim());
        }}
      >
        <input
          type="email"
          className="grow"
          placeholder="or another seeded email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-label="Email"
        />
        <button type="submit" disabled={loading || !email.trim()}>
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      {error ? <Banner tone="critical">{error}</Banner> : null}
    </section>
  );
}

function ContextStrip() {
  const { data: system } = useSystem();
  if (!system) return null;
  return (
    <div className="context-strip" aria-label="Environment">
      {/* Synthetic data and a simulated ERP boundary are labelled in the product itself,
          not only in the README. A demo that hides this is misleading. */}
      <span className="badge demo" title="All data is synthetic; the ERP boundary is simulated.">
        synthetic data · simulated ERP
      </span>
      <span
        className={`badge ${system.scripted_model ? 'scripted' : 'live'}`}
        title={
          system.scripted_model
            ? 'The deterministic development model returns scripted answers. It verifies the workflow, not answer quality.'
            : 'Investigations call a hosted model.'
        }
      >
        {system.scripted_model ? 'scripted development model' : `${system.llm_provider} · ${system.llm_model}`}
      </span>
      <span className="badge" title="The day 'overdue' and 'effective' are evaluated on, for you and the agent alike.">
        business date {day(system.business_date)}
      </span>
      {!system.writes_enabled ? <span className="badge">approved writes disabled</span> : null}
      {!system.agent_enabled ? <span className="badge">investigations disabled</span> : null}
    </div>
  );
}

export default function App() {
  const { me, restoring, signOut } = useSession();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            OP
          </span>
          OpsPilot
        </div>
        {me ? (
          <>
            <nav aria-label="Main">
              <NavLink to="/" end>
                Inbox
              </NavLink>
              <NavLink to="/runs">Investigations</NavLink>
              <NavLink to="/approvals">Approvals</NavLink>
              <NavLink to="/knowledge">Knowledge</NavLink>
              <NavLink to="/metrics">Metrics</NavLink>
            </nav>
            <span className="spacer" />
            <span className="who">
              {me.display_name} · {me.role}
              <small>{me.email}</small>
            </span>
            <button type="button" onClick={signOut}>
              Sign out
            </button>
          </>
        ) : null}
      </header>
      {me ? <ContextStrip /> : null}

      <main id="main">
        {me ? (
          <Routes>
            <Route path="/" element={<OrdersInbox />} />
            <Route path="/orders/:orderId" element={<OrderDetail />} />
            <Route path="/cases/:runId" element={<CaseWorkspace />} />
            <Route path="/runs" element={<RunHistory />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="/knowledge/:versionId/:ordinal" element={<PolicyPassage />} />
            <Route path="/metrics" element={<Metrics />} />
            <Route path="*" element={<p className="empty">This page does not exist.</p>} />
          </Routes>
        ) : restoring ? (
          <p className="empty">Restoring your session…</p>
        ) : (
          <SignIn />
        )}
      </main>
    </div>
  );
}
