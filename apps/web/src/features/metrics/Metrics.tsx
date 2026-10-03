/**
 * Metrics — the database-backed dashboard (ARCHITECTURE §9).
 *
 * Headline numbers are stat tiles; small categorical breakdowns are tables with an
 * inline single-hue bar beside each count, so the number is always there as text.
 * Targets from the evaluation plan are shown as reference notes, never as a pass/fail
 * verdict: scripted development runs would "pass" any latency or cost target.
 */

import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { MetricsSummary } from '@opspilot/contracts';

import { describeError, request } from '../../api/client';
import { Banner } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { count, day, duration, percent, usd, words } from '../../lib/format';

const WINDOWS = [7, 30, 90] as const;

function Stat({ label, value, note }: { label: string; value: string; note?: string | undefined }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

/** A count table with a one-hue bar per row. One series: no legend, the title names it. */
function Breakdown({
  title,
  rows,
  empty,
}: {
  title: string;
  rows: [string, number][];
  empty: string;
}) {
  const max = Math.max(1, ...rows.map(([, value]) => value));
  return (
    <section className="card flush">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="empty">{empty}</p>
      ) : (
        <table>
          <tbody>
            {rows.map(([name, value]) => (
              <tr key={name}>
                <td className="mono small">{name}</td>
                <td style={{ width: '45%' }}>
                  <div className="bar-cell" title={`${name}: ${value}`}>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${(value / max) * 100}%` }} />
                    </div>
                  </div>
                </td>
                <td className="num">{count(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function sorted(record: Record<string, number>): [string, number][] {
  return Object.entries(record).sort((a, b) => b[1] - a[1]);
}

export default function Metrics() {
  const { api } = useSession();
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);
  const { data: m, error, isPending } = useQuery({
    queryKey: ['metrics', api.organizationId, days],
    queryFn: () => request<MetricsSummary>(`/metrics/summary?days=${days}`, api),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Metrics</h1>
          <p>
            {m
              ? `${day(m.window_from)} – ${day(m.window_to)}, your organization only.`
              : 'Quality, cost, latency and approval outcomes for your organization.'}
          </p>
        </div>
        <div className="actions">
          <div className="segmented" role="radiogroup" aria-label="Window">
            {WINDOWS.map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={days === value}
                className={days === value ? 'on' : ''}
                onClick={() => setDays(value)}
              >
                {value} days
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? <Banner tone="critical">Could not load metrics. {describeError(error)}</Banner> : null}
      {isPending && !m ? <p className="empty">Loading metrics…</p> : null}
      {m ? (
        <>
          {m.truncated ? (
            <Banner tone="warning">More runs than one request aggregates; figures cover the newest runs only.</Banner>
          ) : null}
          {m.reconciliation_required > 0 ? (
            <Banner tone="critical" title="Uncertain writes.">
              {m.reconciliation_required} approved task{m.reconciliation_required === 1 ? '' : 's'} have an unknown
              downstream outcome. The worker reconciles by idempotency key before any retry.
            </Banner>
          ) : null}

          <section className="stack" style={{ gap: '0.5rem' }}>
            <h2>Investigations</h2>
            <div className="kpis">
              <Stat label="Runs" value={count(m.run_count)} note={m.in_flight ? `${m.in_flight} in flight` : undefined} />
              <Stat label="Completed" value={count(m.completed)} />
              <Stat label="Awaiting approval" value={count(m.needs_review)} />
              <Stat label="Failed" value={count(m.failed)} note={m.cancelled ? `${m.cancelled} cancelled` : undefined} />
              <Stat label="Incomplete" value={count(m.incomplete)} note="stopped at a bound, or claims removed" />
              <Stat
                label="Citation support"
                value={percent(m.citation_support_ratio)}
                note={`${m.citations_unsupported} of ${m.citations_cited} cited ids unsupported · target ≥ 95%`}
              />
            </div>
          </section>

          <section className="stack" style={{ gap: '0.5rem' }}>
            <h2>Cost and latency</h2>
            <div className="kpis">
              <Stat label="Mean cost per run" value={usd(m.mean_cost_usd)} note="estimate · target < $0.02" />
              <Stat label="p95 cost per run" value={usd(m.p95_cost_usd)} note={`total ${usd(m.total_cost_usd)}`} />
              <Stat label="p95 run duration" value={duration(m.p95_duration_ms)} note="target < 30 s at 3 users" />
              <Stat label="p95 model latency" value={duration(m.p95_provider_latency_ms)} note={`mean ${duration(m.mean_provider_latency_ms)}`} />
              <Stat label="p95 queue wait" value={duration(m.p95_queue_wait_ms)} note="created → claimed by a worker" />
              <Stat label="Tokens" value={count(m.tokens_in + m.tokens_out)} note={`${count(m.retries)} retries · ${count(m.tool_calls)} lookups`} />
            </div>
          </section>

          <section className="stack" style={{ gap: '0.5rem' }}>
            <h2>Approvals and execution</h2>
            <div className="kpis">
              <Stat label="Proposals" value={count(m.proposals_created)} note={`${m.proposals_pending} pending`} />
              <Stat label="Approval rate" value={percent(m.approval_rate)} note={`${m.proposals_approved} approved · ${m.proposals_rejected} rejected`} />
              <Stat label="Expired" value={count(m.proposals_expired)} note="not decided within 24 h" />
              <Stat label="Tasks created" value={count(m.executions_succeeded)} note="in the simulated ERP" />
              <Stat label="Execution failures" value={count(m.executions_failed)} />
              <Stat
                label="Feedback"
                value={count(Object.values(m.feedback).reduce((a, b) => a + b, 0))}
                note={Object.entries(m.feedback).map(([k, v]) => `${v} ${words(k)}`).join(' · ') || 'none yet'}
              />
            </div>
          </section>

          <div className="grid-2">
            <Breakdown title="Why runs stopped short" rows={sorted(m.by_incomplete_reason)} empty="No incomplete runs." />
            <Breakdown title="Run error codes" rows={sorted(m.by_error_code)} empty="No error codes recorded." />
            <Breakdown
              title="Lookups by tool (successful)"
              rows={Object.entries(m.tool_outcomes).map(([tool, outcome]) => [tool, outcome.ok] as [string, number])}
              empty="No lookups recorded."
            />
            <Breakdown title="Lookup errors" rows={sorted(m.tool_errors)} empty="No lookup errors." />
          </div>

          <section className="card flush">
            <h3>By model and prompt version</h3>
            {m.by_model.length === 0 ? (
              <p className="empty">No finished runs in this window.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Provider</th>
                      <th>Model</th>
                      <th>Prompt</th>
                      <th className="num">Runs</th>
                      <th className="num">Mean cost</th>
                      <th className="num">p95 duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.by_model.map((row) => (
                      <tr key={`${row.provider}|${row.model_name}|${row.prompt_version}`}>
                        <td>{row.provider}</td>
                        <td className="mono small">{row.model_name}</td>
                        <td className="mono small">{row.prompt_version}</td>
                        <td className="num">{row.runs}</td>
                        <td className="num">{usd(row.mean_cost_usd)}</td>
                        <td className="num">{duration(row.p95_duration_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted small" style={{ padding: '0.6rem 1.1rem' }}>
              Results are only comparable within one provider, model and prompt version. Targets come from the
              evaluation plan and are not claims about these numbers.
            </p>
          </section>
        </>
      ) : null}
    </div>
  );
}
