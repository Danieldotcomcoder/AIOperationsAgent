/**
 * Approval inbox — the only path to a write.
 *
 * A supervisor approves the exact stored payload: the request echoes the payload hash
 * this screen rendered, and a mismatch is refused (ADR 0004). The buttons mirror what
 * the server will accept; the server enforces it regardless.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import type { Action, ActionStatus, DecisionRequest } from '@opspilot/contracts';
import { isApprovable } from '@opspilot/contracts';

import { ApiError, describeError, request } from '../../api/client';
import { Banner, Status } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { dateTime, words } from '../../lib/format';

interface FollowupPayload {
  order_id?: string;
  order_number?: string;
  description?: string;
  due_at?: string;
}

const FILTERS: (ActionStatus | '')[] = [
  'pending',
  '',
  'approved',
  'executing',
  'reconciliation_required',
  'succeeded',
  'rejected',
  'expired',
  'failed',
];

function Proposal({ action, highlighted }: { action: Action; highlighted: boolean }) {
  const { api, me } = useSession();
  const cache = useQueryClient();
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const payload = action.payload as FollowupPayload;
  const pending = action.status === 'pending';
  const approvable = isApprovable(action);

  async function decide(approve: boolean) {
    setBusy(true);
    setError('');
    try {
      const body: DecisionRequest = { payload_hash: action.payload_hash, comment: comment.trim() || null };
      await request(`/actions/${action.id}/${approve ? 'approve' : 'reject'}`, api, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      await cache.invalidateQueries({ queryKey: ['actions'] });
      await cache.invalidateQueries({ queryKey: ['run', api.organizationId, action.run_id] });
      await cache.invalidateQueries({ queryKey: ['action', api.organizationId, action.id] });
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.isStaleApproval
          ? 'This proposal is stale or expired and can no longer be approved. Start a new investigation.'
          : describeError(caught, 'Decision failed. Refresh before trying again.'),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      id={action.id}
      className="card proposal"
      style={highlighted ? { outline: '2px solid var(--accent)' } : undefined}
    >
      <div className="row between">
        <h2>
          Follow-up task for{' '}
          {payload.order_id ? (
            <Link className="mono" to={`/orders/${payload.order_id}`}>
              {payload.order_number ?? 'order'}
            </Link>
          ) : (
            'an order'
          )}
        </h2>
        <Status value={action.status} />
      </div>
      <p>
        <strong>{payload.description ?? 'No description'}</strong>
      </p>
      <dl className="facts small">
        <dt>Assigned to</dt>
        <dd>{action.assignee_name ?? 'unknown member'}</dd>
        <dt>Due</dt>
        <dd>{dateTime(payload.due_at)}</dd>
        <dt>Proposed</dt>
        <dd>
          {dateTime(action.created_at)} · <Link to={`/cases/${action.run_id}`}>see the investigation</Link>
        </dd>
        <dt>Decide by</dt>
        <dd>{dateTime(action.expires_at)}</dd>
        <dt>Bound to</dt>
        <dd>order record version {action.source_record_version}</dd>
      </dl>
      <details style={{ marginTop: '0.6rem' }}>
        <summary className="small">Exact payload and verification hash</summary>
        <pre style={{ marginTop: '0.4rem' }}>{JSON.stringify(action.payload, null, 2)}</pre>
        <p className="small muted">
          sha256 <code style={{ overflowWrap: 'anywhere' }}>{action.payload_hash}</code>
        </p>
      </details>

      {pending && action.decision_expired ? (
        <Banner tone="warning">Expired. Start a new investigation to propose another task.</Banner>
      ) : null}
      {pending && action.source_changed ? (
        <Banner tone="warning">The order changed since this was proposed. Start a new investigation before approving.</Banner>
      ) : null}
      {pending && !action.payload_valid ? (
        <Banner tone="critical">Payload verification failed. A new investigation is required.</Banner>
      ) : null}
      {action.status === 'approved' || action.status === 'executing' ? (
        <Banner tone="info">Approved and queued. The task has not been confirmed by the ERP yet.</Banner>
      ) : null}
      {action.status === 'reconciliation_required' ? (
        <Banner tone="warning">
          The outcome is uncertain. The worker will look up the idempotency key before any further write.
        </Banner>
      ) : null}
      {action.downstream_task_id ? (
        <Banner tone="good">
          Task <code>{action.downstream_task_id}</code> exists in the simulated ERP.
        </Banner>
      ) : null}
      {action.execution_error ? (
        <Banner tone="critical">Execution status: {words(action.execution_error)}</Banner>
      ) : null}

      {pending && me?.can_approve ? (
        <div className="stack" style={{ gap: '0.5rem', marginTop: '0.75rem' }}>
          <label className="field">
            Decision note (optional)
            <textarea maxLength={1000} value={comment} onChange={(event) => setComment(event.target.value)} />
          </label>
          <div className="row">
            <button type="button" className="primary" disabled={busy || !approvable} onClick={() => void decide(true)}>
              Approve this exact payload
            </button>
            <button type="button" disabled={busy || action.decision_expired} onClick={() => void decide(false)}>
              Reject
            </button>
          </div>
        </div>
      ) : null}
      {pending && !me?.can_approve ? <p className="muted small">Only a supervisor may approve or reject.</p> : null}
      {error ? <Banner tone="critical">{error}</Banner> : null}
    </article>
  );
}

export default function Approvals() {
  const { api } = useSession();
  const location = useLocation();
  const target = location.hash.slice(1);
  // Arriving from a case link: show every status so the linked proposal is present.
  const [filter, setFilter] = useState<ActionStatus | ''>(target ? '' : 'pending');
  const { data, error, isPending } = useQuery({
    queryKey: ['actions', api.organizationId, filter],
    queryFn: () => request<Action[]>(`/actions?limit=50${filter ? `&status_filter=${filter}` : ''}`, api),
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (target && data) document.getElementById(target)?.scrollIntoView({ block: 'center' });
  }, [target, data]);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Approval inbox</h1>
          <p>Nothing is written to the simulated ERP until a supervisor approves the exact payload.</p>
        </div>
        <div className="actions">
          <label className="field">
            Show
            <select value={filter} onChange={(event) => setFilter(event.target.value as ActionStatus | '')}>
              {FILTERS.map((value) => (
                <option key={value} value={value}>
                  {value ? words(value) : 'all statuses'}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>
      {isPending ? <p className="empty">Loading proposals…</p> : null}
      {error ? <Banner tone="critical">Could not load proposals. {describeError(error)}</Banner> : null}
      {data?.length === 0 ? (
        <p className="empty">
          {filter === 'pending' ? 'Nothing is waiting for a decision.' : 'No proposals match this view.'}
        </p>
      ) : null}
      {data?.map((action) => <Proposal key={action.id} action={action} highlighted={action.id === target} />)}
    </div>
  );
}
