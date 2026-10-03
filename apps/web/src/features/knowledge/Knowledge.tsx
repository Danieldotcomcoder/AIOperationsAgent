/**
 * Knowledge — policy upload, revisions, indexing status and deletion.
 *
 * Investigations retrieve only indexed versions effective on the business date. The
 * upload form defaults to that date and says so, because a policy dated after it is
 * correctly invisible to the agent — and otherwise looks like a retrieval bug.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import type { PolicyDocument } from '@opspilot/contracts';

import { describeError, request } from '../../api/client';
import { Banner, Status } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { dateTime, day } from '../../lib/format';
import { useSystem } from '../../lib/system';

export default function Knowledge() {
  const { api, me } = useSession();
  const { data: system } = useSystem();
  const client = useQueryClient();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState<PolicyDocument | null>(null);
  const policies = useQuery({
    queryKey: ['documents', api.organizationId],
    queryFn: () => request<PolicyDocument[]>('/documents', api),
    refetchInterval: (query) =>
      query.state.data?.some((policy) => policy.status === 'pending' || policy.status === 'indexing') ? 2000 : false,
  });
  const businessDate = system?.business_date ?? '';

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = new FormData(form);
    const file = body.get('file');
    // Browsers often send .md with no type; the API accepts only text/markdown or text/plain.
    if (file instanceof File && !file.type) {
      body.set(
        'file',
        new File([file], file.name, {
          type: file.name.toLowerCase().endsWith('.md') ? 'text/markdown' : 'text/plain',
        }),
      );
    }
    if (revision) body.set('document_id', revision.id);
    setBusy(true);
    setError('');
    try {
      await request('/documents', api, { method: 'POST', body });
      form.reset();
      setRevision(null);
      await client.invalidateQueries({ queryKey: ['documents'] });
    } catch (caught) {
      setError(describeError(caught, 'Upload failed.'));
    } finally {
      setBusy(false);
    }
  }

  async function act(policy: PolicyDocument, retry: boolean) {
    if (!retry && !window.confirm(`Delete “${policy.title}” and all its indexed passages?`)) return;
    setBusy(true);
    setError('');
    try {
      await request(`/documents/${policy.id}${retry ? '/retry' : ''}`, api, { method: retry ? 'POST' : 'DELETE' });
      await client.invalidateQueries({ queryKey: ['documents'] });
    } catch (caught) {
      setError(describeError(caught, 'Request failed.'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Policy knowledge</h1>
          <p>
            Investigations cite only indexed policy versions in force on the business date
            {businessDate ? ` (${day(businessDate)})` : ''}.
          </p>
        </div>
      </div>

      {me?.can_approve ? (
        <section className="card">
          <h2>{revision ? `New revision of “${revision.title}”` : 'Upload a policy'}</h2>
          <form className="stack" style={{ gap: '0.6rem' }} onSubmit={(event) => void upload(event)} key={`${revision?.id ?? 'new'}-${businessDate}`}>
            <div className="row">
              <label className="field grow">
                Title
                <input name="title" required maxLength={300} defaultValue={revision?.title ?? ''} />
              </label>
              <label className="field">
                Effective from
                <input name="effective_from" type="date" required defaultValue={businessDate} />
              </label>
            </div>
            <label className="field">
              Markdown or plain-text file (UTF-8, up to 2 MB)
              <input name="file" type="file" accept=".md,.txt,text/plain,text/markdown" required />
            </label>
            <p className="muted small">
              A policy takes effect on its date: choose one on or before the business date for investigations to use
              it. Revisions must start after the previous version. A synthetic starter policy is in{' '}
              <code>data/policies/delayed-shipments.md</code>.
            </p>
            <div className="row">
              <button className="primary" disabled={busy}>
                {busy ? 'Working…' : revision ? 'Upload revision' : 'Upload'}
              </button>
              {revision ? (
                <button type="button" onClick={() => setRevision(null)}>
                  Cancel revision
                </button>
              ) : null}
            </div>
          </form>
        </section>
      ) : (
        <Banner tone="info">Supervisors upload and manage policies. You can read every indexed passage an investigation cites.</Banner>
      )}

      {error || policies.error ? (
        <Banner tone="critical">{error || `Could not load policies. ${describeError(policies.error)}`}</Banner>
      ) : null}
      {policies.isPending ? <p className="empty">Loading policies…</p> : null}
      {policies.data?.length === 0 ? <p className="empty">No policies uploaded yet.</p> : null}

      {policies.data?.map((policy) => (
        <section key={policy.id} className="card">
          <div className="row between">
            <h2>{policy.title}</h2>
            <Status value={policy.status === 'pending' ? 'indexing' : policy.status} />
          </div>
          <p className="small">
            {policy.active_version
              ? `Version ${policy.active_version} in force since ${day(policy.effective_from)} · ${policy.chunk_count} passages`
              : 'No version is in force on the business date.'}
          </p>
          <p className="muted small">
            Latest revision {policy.latest_version ?? '—'}, effective {day(policy.latest_effective_from)} · updated{' '}
            {dateTime(policy.updated_at)}
            {policy.embedding_mode ? ` · index ${policy.embedding_mode}` : ''}
          </p>
          {policy.embedding_mode === 'fake-hash-v1' ? (
            <p className="muted small">Development retrieval matches words, not meaning; semantic quality is not evaluated.</p>
          ) : null}
          {me?.can_approve ? (
            <div className="row" style={{ marginTop: '0.5rem' }}>
              <button type="button" disabled={busy} onClick={() => setRevision(policy)}>
                Add revision
              </button>
              {policy.status === 'pending' || policy.status === 'failed' ? (
                <button type="button" disabled={busy} onClick={() => void act(policy, true)}>
                  Retry indexing
                </button>
              ) : null}
              <button type="button" className="danger" disabled={busy} onClick={() => void act(policy, false)}>
                Delete
              </button>
            </div>
          ) : null}
        </section>
      ))}
    </div>
  );
}
