/** One cited policy passage, exactly as the investigation was shown it. */

import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import type { PolicyPassage as Passage } from '@opspilot/contracts';

import { request } from '../../api/client';
import { Banner } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { day } from '../../lib/format';

export default function PolicyPassage() {
  const { versionId, ordinal } = useParams();
  const { api } = useSession();
  const { data, error } = useQuery({
    queryKey: ['passage', api.organizationId, versionId, ordinal],
    queryFn: () => request<Passage>(`/documents/versions/${versionId}/chunks/${ordinal}`, api),
  });
  if (error) return <Banner tone="warning">This policy passage is unavailable, or has been deleted.</Banner>;
  if (!data) return <p className="empty">Loading passage…</p>;
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="small">
            <Link to="/knowledge">Policy knowledge</Link> / passage
          </p>
          <h1>
            {data.title} · version {data.version}
          </h1>
          <p>
            Effective from {day(data.effective_from)}
            {data.effective_to ? ` until ${day(data.effective_to)} (exclusive)` : ''}
          </p>
        </div>
      </div>
      <section className="card">
        <h2>{data.heading_path || 'Passage'}</h2>
        <pre style={{ fontFamily: 'inherit', fontSize: '0.95rem', marginTop: '0.5rem' }}>{data.text}</pre>
      </section>
    </div>
  );
}
