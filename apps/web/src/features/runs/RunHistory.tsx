/**
 * Investigations — run history for the caller's organization.
 *
 * Status, elapsed time, token cost, errors and the model that answered: the record an
 * operator needs to tell "the model was wrong" from "the model was stopped".
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { RunStatus, RunSummary } from '@opspilot/contracts';
import { isRunFinished } from '@opspilot/contracts';

import { describeError, request } from '../../api/client';
import { Banner, Status } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { count, dateTime, duration, usd, words } from '../../lib/format';

const PAGE = 25;

/** "nvidia/nemotron-3-ultra-550b-a55b:free" -> "nemotron-3-ultra…"; full name on hover. */
function shortModel(name: string): string {
  const base = name.split('/').pop()?.replace(/:free$/, '') ?? name;
  return base.length > 18 ? `${base.slice(0, 16)}…` : base;
}
const FILTERS: (RunStatus | '')[] = ['', 'queued', 'investigating', 'needs_review', 'completed', 'failed', 'cancelled'];

export default function RunHistory() {
  const { api } = useSession();
  const navigate = useNavigate();
  const [status, setStatus] = useState<RunStatus | ''>('');
  const [page, setPage] = useState(0);

  const params = new URLSearchParams({ limit: String(PAGE + 1), offset: String(page * PAGE) });
  if (status) params.set('status_filter', status);
  const runs = useQuery({
    queryKey: ['runs', api.organizationId, status, page],
    queryFn: () => request<RunSummary[]>(`/runs?${params.toString()}`, api),
    placeholderData: keepPreviousData,
    // Keep in-flight rows moving without a manual refresh.
    refetchInterval: (query) =>
      query.state.data?.some((run) => !isRunFinished(run.status)) ? 2000 : false,
  });
  const rows = (runs.data ?? []).slice(0, PAGE);

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Investigations</h1>
          <p>Every run in your organization, newest first. Costs are estimates from a price table.</p>
        </div>
        <div className="actions">
          <label className="field">
            Status
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as RunStatus | '');
                setPage(0);
              }}
            >
              {FILTERS.map((value) => (
                <option key={value} value={value}>
                  {value ? words(value === 'needs_review' ? 'awaiting_approval' : value) : 'All statuses'}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {runs.error ? <Banner tone="critical">Could not load investigations. {describeError(runs.error)}</Banner> : null}

      <section className="card flush">
        {runs.isPending ? (
          <p className="empty">Loading investigations…</p>
        ) : rows.length === 0 ? (
          <p className="empty">
            No investigations{status ? ' with this status' : ' yet'}. Start one from the <Link to="/">inbox</Link>.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Order</th>
                  <th>Request</th>
                  <th>Status</th>
                  <th>Proposal</th>
                  <th className="num">Time · tokens · cost</th>
                  <th>Model</th>
                  <th>Feedback</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => (
                  <tr key={run.id} className="clickable" onClick={() => navigate(`/cases/${run.id}`)}>
                    <td className="nowrap">
                      <Link to={`/cases/${run.id}`} onClick={(event) => event.stopPropagation()}>
                        {dateTime(run.created_at)}
                      </Link>
                    </td>
                    <td className="mono nowrap">{run.order_number ?? '—'}</td>
                    <td>
                      <div className="truncate" title={run.request_text}>
                        {run.request_text}
                      </div>
                      <div className="muted small">by {run.initiated_by_me ? 'you' : run.initiated_by_name ?? 'a former member'}</div>
                    </td>
                    <td>
                      <Status value={run.status} />
                      {run.incomplete_reason ? (
                        <div className="muted small">incomplete: {words(run.incomplete_reason)}</div>
                      ) : null}
                      {run.error_code ? <div className="small error-text mono">{run.error_code}</div> : null}
                    </td>
                    <td>{run.proposal_status ? <Status value={run.proposal_status} /> : <span className="muted">—</span>}</td>
                    <td className="num">
                      {duration(run.duration_ms)}
                      <div className="muted small">
                        {count(run.tokens_in + run.tokens_out)} tok · {usd(run.cost_usd)}
                      </div>
                    </td>
                    <td className="small nowrap" title={`${run.provider} · ${run.model_name} · prompt ${run.prompt_version}`}>
                      {shortModel(run.model_name)}
                      <div className="muted">{run.prompt_version}</div>
                    </td>
                    <td className="small nowrap">
                      {run.feedback_ratings.length > 0 ? run.feedback_ratings.map(words).join(', ') : <span className="muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pager">
          <span className="muted small">Page {page + 1}</span>
          <button type="button" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Previous
          </button>
          <button
            type="button"
            disabled={(runs.data?.length ?? 0) <= PAGE}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </section>
    </div>
  );
}
