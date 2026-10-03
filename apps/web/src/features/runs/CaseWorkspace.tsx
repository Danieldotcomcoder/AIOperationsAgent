/**
 * Case workspace — the screen an analyst reads before deciding anything.
 *
 * Three things it must never do, each of which is a way to mislead someone who is
 * about to act on what they see:
 *
 *  1. present an incomplete answer as a complete one;
 *  2. show a claim without the evidence behind it;
 *  3. imply an action has happened when it is only proposed.
 *
 * So: incomplete runs carry a banner naming the reason, every finding renders its
 * evidence ids, and the proposal is labelled with its real approval/execution state,
 * with no control here that could execute it.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { Action, FeedbackRequest, RunDetail, Usefulness } from '@opspilot/contracts';
import { SETTLED_ACTION_STATUSES, isRunFinished } from '@opspilot/contracts';

import { ApiError, describeError, request } from '../../api/client';
import { EvidenceChip, EvidenceList } from '../../components/Evidence';
import { Banner, Status } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { dateTime, duration, usd, words } from '../../lib/format';

/** Why a run stopped short, in words an operations analyst can act on. */
const INCOMPLETE_COPY: Record<string, string> = {
  budget_exhausted: 'The token budget for this investigation ran out before it finished.',
  turn_limit: 'The investigation reached its limit of model calls before producing an answer.',
  tool_limit:
    'The investigation reached its limit of data lookups. The answer was formed without every lookup it asked for.',
  deadline: 'The investigation ran out of time.',
  provider_unavailable: 'The model provider was unavailable. Nothing was concluded; try again later.',
  insufficient_evidence:
    'One or more claims cited evidence this run was never shown, and were removed. Treat the remaining findings as partial.',
  cancelled: 'This investigation was cancelled.',
};

const ERROR_COPY: Record<string, string> = {
  'provider.invalid_output': "The model's answer did not match the required structure, so it was rejected.",
  'auth.forbidden': 'The requesting membership was no longer active when the investigation ran.',
  'feature.agent_disabled': 'Investigations were disabled while this one was running.',
  'worker.lost': 'The worker running this investigation stopped; the run was closed after its deadline.',
};

const FAILURE_CLASSES = [
  ['', 'No category'],
  ['wrong_facts', 'Wrong order facts'],
  ['missing_policy', 'Missed the applicable policy'],
  ['unsupported_claim', 'Claim not supported by its evidence'],
  ['should_abstain', 'Should have said it did not know'],
  ['bad_proposal', 'Proposed task not useful'],
  ['other', 'Other'],
] as const;

function FeedbackPanel({ run }: { run: RunDetail }) {
  const { api } = useSession();
  const cache = useQueryClient();
  const [usefulness, setUsefulness] = useState<Usefulness>('useful');
  const [failureClass, setFailureClass] = useState('');
  const [correction, setCorrection] = useState('');
  const submit = useMutation({
    mutationFn: () => {
      const body: FeedbackRequest = {
        usefulness,
        failure_class: failureClass || null,
        correction_text: correction.trim() || null,
      };
      return request(`/runs/${run.id}/feedback`, api, { method: 'POST', body: JSON.stringify(body) });
    },
    onSuccess: () => {
      setCorrection('');
      setFailureClass('');
      void cache.invalidateQueries({ queryKey: ['run', api.organizationId, run.id] });
    },
  });

  return (
    <section className="card">
      <h2>Feedback</h2>
      {run.feedback.length > 0 ? (
        <ul className="plain stack" style={{ gap: '0.5rem', marginBottom: '0.9rem' }}>
          {run.feedback.map((item, index) => (
            <li key={index} className="small">
              <strong>{words(item.usefulness)}</strong>
              {item.failure_class ? ` · ${words(item.failure_class)}` : ''} — {item.mine ? 'you' : item.author},{' '}
              {dateTime(item.created_at)}
              {item.correction_text ? <p className="muted">{item.correction_text}</p> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">
          Was this investigation useful? Feedback is part of the pilot record and is
          reviewed alongside the evaluation results.
        </p>
      )}
      {run.trace_pruned_at ? (
        <p className="muted small">Feedback is closed: this run's content was removed under retention.</p>
      ) : (
        <form
          className="stack"
          style={{ gap: '0.6rem' }}
          onSubmit={(event) => {
            event.preventDefault();
            submit.mutate();
          }}
        >
          <div className="segmented" role="radiogroup" aria-label="Usefulness">
            {(['useful', 'partly', 'not_useful'] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={usefulness === value}
                className={usefulness === value ? 'on' : ''}
                onClick={() => setUsefulness(value)}
              >
                {words(value)}
              </button>
            ))}
          </div>
          <label className="field">
            What went wrong (optional)
            <select value={failureClass} onChange={(event) => setFailureClass(event.target.value)}>
              {FAILURE_CLASSES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Correction (optional)
            <textarea
              maxLength={2000}
              value={correction}
              onChange={(event) => setCorrection(event.target.value)}
              placeholder="What should the answer have said?"
            />
          </label>
          <div className="row">
            <button type="submit" className="primary" disabled={submit.isPending}>
              {submit.isPending ? 'Recording…' : 'Record feedback'}
            </button>
            {submit.isSuccess ? <span className="small">Recorded. Thank you.</span> : null}
          </div>
          {submit.error ? <Banner tone="critical">{describeError(submit.error)}</Banner> : null}
        </form>
      )}
    </section>
  );
}

function ProposalCard({ run }: { run: RunDetail }) {
  const { api, me } = useSession();
  const draft = run.result?.proposed_action;
  const { data: action } = useQuery({
    queryKey: ['action', api.organizationId, run.proposal_id],
    queryFn: () => request<Action>(`/actions/${run.proposal_id}`, api),
    enabled: Boolean(run.proposal_id),
    refetchInterval: (query) =>
      SETTLED_ACTION_STATUSES.includes(query.state.data?.status ?? 'pending') ? false : 3000,
  });
  if (!draft) return null;

  const payload = action?.payload as { description?: string; due_at?: string } | undefined;
  return (
    <section className="card proposal">
      <div className="row between">
        <h2>Proposed follow-up task</h2>
        {action ? <Status value={action.status} /> : null}
      </div>
      <p>
        <strong>{payload?.description ?? draft.description}</strong>
      </p>
      <p className="muted small">
        {payload?.due_at ? `Due ${dateTime(payload.due_at)}` : `Due in ${draft.due_in_days} days`} ·{' '}
        {draft.rationale}
      </p>
      {!run.proposal_id ? (
        <Banner tone="info">
          No task was proposed for approval: this investigation was not bound to a specific
          order, so there is no record a proposal could be checked against.
        </Banner>
      ) : action?.status === 'pending' ? (
        <p className="small">
          Awaiting a supervisor. Nothing has been written.{' '}
          {me?.can_approve ? <Link to={`/approvals#${action.id}`}>Review it in Approvals</Link> : null}
        </p>
      ) : action?.status === 'succeeded' ? (
        <Banner tone="good">
          Approved and executed: task <code>{action.downstream_task_id}</code> was created in
          the simulated ERP.
        </Banner>
      ) : action ? (
        <p className="small">
          <Link to={`/approvals#${action.id}`}>See the decision and execution record</Link>.
        </p>
      ) : (
        <p className="muted small">Loading proposal status…</p>
      )}
    </section>
  );
}

export default function CaseWorkspace() {
  const { runId } = useParams<{ runId: string }>();
  const { api } = useSession();
  const cache = useQueryClient();
  const runKey = ['run', api.organizationId, runId];

  const { data: run, error } = useQuery({
    queryKey: runKey,
    queryFn: () => request<RunDetail>(`/runs/${runId}`, api),
    // Poll while the run is in flight, then stop. Streaming is out of scope for v1.
    refetchInterval: (query) => (query.state.data && isRunFinished(query.state.data.status) ? false : 1000),
  });

  const cancel = useMutation({
    mutationFn: () => request(`/runs/${runId}/cancel`, api, { method: 'POST' }),
    onSettled: () => cache.invalidateQueries({ queryKey: runKey }),
  });

  if (error) {
    return (
      <Banner tone="critical" title="Could not load this investigation.">
        {error instanceof ApiError && error.status === 404
          ? 'It does not exist in your organization.'
          : describeError(error)}
      </Banner>
    );
  }
  if (!run) return <p className="empty">Loading investigation…</p>;

  const inFlight = !isRunFinished(run.status);
  const result = run.result;
  const validation = result?.validation;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="small">
            <Link to="/runs">Investigations</Link> / case
          </p>
          <h1>
            Investigation{' '}
            {run.order_number && run.order_id ? (
              <Link className="mono" to={`/orders/${run.order_id}`}>
                {run.order_number}
              </Link>
            ) : null}
          </h1>
          <p>
            Started {dateTime(run.created_at)} by {run.initiated_by_me ? 'you' : run.initiated_by_name ?? 'a former member'}
          </p>
        </div>
        <div className="actions">
          <Status value={run.status} />
          {run.can_cancel ? (
            <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              {cancel.isPending ? 'Requesting cancellation…' : 'Cancel investigation'}
            </button>
          ) : null}
        </div>
      </div>

      <p className="request-quote">{run.request_text}</p>

      {inFlight ? (
        <Banner tone="info">
          Working: {run.steps.length > 0 ? `${run.steps.length} steps so far.` : 'waiting for a worker.'} This page
          updates itself.
        </Banner>
      ) : null}
      {inFlight && run.cancel_requested_at ? (
        <Banner tone="info">
          Cancellation requested. The current call will finish; no further calls or proposal will be started.
        </Banner>
      ) : null}
      {cancel.error ? <Banner tone="critical">Could not cancel: {describeError(cancel.error)}</Banner> : null}
      {run.trace_pruned_at ? (
        <Banner tone="info">
          The request, findings and activity were removed under the retention policy. Usage totals and
          approval records remain.
        </Banner>
      ) : null}
      {run.incomplete_reason ? (
        <Banner tone="warning" title="Incomplete.">
          {INCOMPLETE_COPY[run.incomplete_reason] ?? words(run.incomplete_reason)}
        </Banner>
      ) : null}
      {run.error_code ? (
        <Banner tone="critical" title="This investigation failed.">
          {ERROR_COPY[run.error_code] ?? `(${run.error_code})`} Nothing here should be treated as a conclusion.
        </Banner>
      ) : null}

      {result ? (
        <>
          <section className="card">
            <h2>Summary</h2>
            <p>{result.summary}</p>
          </section>

          <section className="card">
            <h2>Findings</h2>
            {(result.findings ?? []).length === 0 ? (
              <p className="muted">No findings were established.</p>
            ) : (
              <ul className="plain findings">
                {(result.findings ?? []).map((finding, index) => (
                  <li key={index}>
                    {/* Fact and inference are graded differently in review, so they are
                        visually distinct rather than blended into one list. */}
                    <span className={`kind ${finding.kind === 'observed_fact' ? 'fact' : 'inference'}`}>
                      {finding.kind === 'observed_fact' ? 'fact' : 'inference'}
                    </span>
                    <div>
                      {finding.statement}
                      <EvidenceList ids={finding.evidence_ids ?? []} orderId={run.order_id} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {(result.policy_citations ?? []).length > 0 ? (
            <section className="card">
              <h2>Policy references</h2>
              <ul className="plain stack" style={{ gap: '0.5rem' }}>
                {(result.policy_citations ?? []).map((citation) => (
                  <li key={citation.citation_id}>
                    <EvidenceChip id={citation.citation_id} /> {citation.relevance}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {(result.missing_information ?? []).length > 0 ? (
            <section className="card">
              <h2>What is still unknown</h2>
              <ul>
                {(result.missing_information ?? []).map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <ProposalCard run={run} />
        </>
      ) : null}

      <section className="card flush">
        <h2>Activity</h2>
        {run.steps.length === 0 ? (
          <p className="empty">No steps recorded yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Step</th>
                  <th>Tool</th>
                  <th className="num">Rows</th>
                  <th className="num">Tokens in / out</th>
                  <th className="num">Latency</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {run.steps.map((step) => (
                  <tr key={step.ordinal}>
                    <td>{step.ordinal + 1}</td>
                    <td>{words(step.step_type)}</td>
                    <td className="mono">{step.tool_name ?? ''}</td>
                    <td className="num">{step.result_row_count ?? ''}</td>
                    <td className="num">{step.tokens_in || step.tokens_out ? `${step.tokens_in} / ${step.tokens_out}` : ''}</td>
                    <td className="num">{duration(step.latency_ms)}</td>
                    <td className="mono small">{step.error_code ?? 'ok'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small" style={{ padding: '0.6rem 1.1rem' }}>
          {run.turn_count} model calls · {run.tool_call_count} lookups · {run.retry_count} retries ·{' '}
          {run.tokens_in}/{run.tokens_out} tokens · {duration(run.duration_ms)} ·{' '}
          <span title="Estimated from a price table, not billed truth">{usd(run.cost_usd)} estimated</span>
          {validation ? ` · ${validation.cited} citations, ${validation.unsupported.length} unsupported` : ''} ·{' '}
          {run.provider}/{run.model_name} · prompt {run.prompt_version}
        </p>
      </section>

      {!inFlight ? <FeedbackPanel run={run} /> : null}
    </div>
  );
}
