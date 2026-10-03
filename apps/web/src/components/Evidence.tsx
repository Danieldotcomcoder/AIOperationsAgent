/**
 * Evidence ids, rendered as chips that open what they cite.
 *
 * The id is the citation; the link is a convenience. Orders open their record, and
 * shipment or event ids open the run's order (they carry no order id of their own).
 * Policy ids open the exact passage the run was shown.
 */

import { Link } from 'react-router-dom';

const POLICY = /^pol:([0-9a-f-]{36})#(\d+)$/i;
const ORDER = /^ord:([0-9a-f-]{36})$/i;

export function EvidenceChip({ id, orderId }: { id: string; orderId?: string | null }) {
  const short = id.length > 16 ? `${id.slice(0, 12)}…` : id;
  const policy = POLICY.exec(id);
  if (policy) {
    return (
      <Link className="chip" to={`/knowledge/${policy[1]}/${policy[2]}`} title={id}>
        {short}
      </Link>
    );
  }
  const order = ORDER.exec(id);
  const target = order?.[1] ?? ((id.startsWith('shp:') || id.startsWith('evt:')) && orderId);
  if (target) {
    return (
      <Link className="chip" to={`/orders/${target}`} title={`${id} — open the order`}>
        {short}
      </Link>
    );
  }
  return (
    <code className="chip" title={id}>
      {short}
    </code>
  );
}

export function EvidenceList({ ids, orderId }: { ids: string[]; orderId?: string | null }) {
  if (ids.length === 0) return <span className="muted small">no evidence cited</span>;
  return (
    <span className="chips">
      {ids.map((id) => (
        <EvidenceChip key={id} id={id} orderId={orderId ?? null} />
      ))}
    </span>
  );
}
