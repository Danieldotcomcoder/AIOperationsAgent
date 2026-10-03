/**
 * One order: the facts an investigation reads, and what has happened around it.
 *
 * Everything here comes from the same service the agent's `get_order_context` tool
 * uses, on the same business date, so the analyst and the agent see one record.
 * Carrier notes are untrusted free text: rendered as text, labelled as such.
 */

import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import type { OrderDetail as Order } from '@opspilot/contracts';

import { describeError, request } from '../../api/client';
import { Banner, Status } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { dateTime, day, money, words } from '../../lib/format';
import NewInvestigation from '../runs/NewInvestigation';

export default function OrderDetail() {
  const { orderId } = useParams<{ orderId: string }>();
  const { api } = useSession();
  const { data: order, error } = useQuery({
    queryKey: ['order', api.organizationId, orderId],
    queryFn: () => request<Order>(`/orders/${orderId}`, api),
  });

  if (error) {
    // Another organization's order is a 404, exactly like a missing one.
    return <Banner tone="critical">This order does not exist in your organization. {describeError(error)}</Banner>;
  }
  if (!order) return <p className="empty">Loading order…</p>;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <p className="small">
            <Link to="/">Inbox</Link> / order
          </p>
          <h1 className="mono">{order.order_number}</h1>
          <p>
            {order.customer_name} · {words(order.status)} · promised {day(order.promised_date)}
            {order.days_overdue > 0 ? ` · ${order.days_overdue} days overdue` : ''}
          </p>
        </div>
        {order.has_open_exception ? (
          <span className="status warning">
            <span className="icon" aria-hidden="true">
              !
            </span>
            carrier reported an issue
          </span>
        ) : null}
      </div>

      <section className="card">
        <h2>Investigate this order</h2>
        <NewInvestigation orderNumber={order.order_number} />
      </section>

      <div className="grid-2">
        <section className="card">
          <h2>Order</h2>
          <dl className="facts">
            <dt>Evidence id</dt>
            <dd className="mono small">{order.evidence_id}</dd>
            <dt>Status</dt>
            <dd>{words(order.status)}</dd>
            <dt>Promised</dt>
            <dd>{day(order.promised_date)}</dd>
            <dt>Value</dt>
            <dd>{money(order.total_amount)}</dd>
            <dt title="Approvals bind to this version (ADR 0004)">Record version</dt>
            <dd>{order.record_version}</dd>
          </dl>
        </section>
        <section className="card">
          <h2>Follow-up tasks in the simulated ERP</h2>
          {order.followup_tasks.length === 0 ? (
            <p className="muted small">None. Tasks appear here only after a supervisor approves a proposal.</p>
          ) : (
            <ul className="plain stack" style={{ gap: '0.5rem' }}>
              {order.followup_tasks.map((task) => (
                <li key={task.id}>
                  <strong>{task.description}</strong>
                  <div className="muted small">
                    {words(task.status)} · due {dateTime(task.due_at)} · task{' '}
                    <code>{task.id.slice(0, 8)}</code>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card flush">
        <h2>Lines</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th className="num">Ordered</th>
                <th className="num">Shipped</th>
                <th className="num">Outstanding</th>
                <th className="num">Unit price</th>
              </tr>
            </thead>
            <tbody>
              {order.lines.map((line) => (
                <tr key={line.id}>
                  <td className="mono">{line.sku}</td>
                  <td>{line.product_name}</td>
                  <td className="num">{line.quantity}</td>
                  <td className="num">{line.quantity_shipped}</td>
                  <td className="num">{line.outstanding > 0 ? <strong>{line.outstanding}</strong> : '0'}</td>
                  <td className="num">{money(line.unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card flush">
        <h2>Shipments</h2>
        {order.shipments.length === 0 ? (
          <p className="empty">No shipment has been created for this order.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Carrier</th>
                  <th>Tracking</th>
                  <th>Status</th>
                  <th>Shipped</th>
                  <th>Estimated delivery</th>
                  <th>Delivered</th>
                </tr>
              </thead>
              <tbody>
                {order.shipments.map((shipment) => (
                  <tr key={shipment.id}>
                    <td>{shipment.carrier}</td>
                    <td className="mono">
                      {shipment.tracking_reference ?? <em className="muted">missing</em>}
                    </td>
                    <td>{words(shipment.status)}</td>
                    <td>{dateTime(shipment.shipped_at)}</td>
                    <td>{day(shipment.estimated_delivery)}</td>
                    <td>{dateTime(shipment.delivered_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>Carrier events</h2>
        {order.events.length === 0 ? (
          <p className="muted">No carrier events recorded.</p>
        ) : (
          <ul className="timeline">
            {order.events.map((event) => (
              <li key={event.id}>
                <span className="muted small">{dateTime(event.occurred_at)}</span>
                <strong>{words(event.event_type)}</strong>
                <span>
                  {event.note ? <div className="untrusted">{event.note}</div> : <span className="muted small">no note</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Investigations of this order</h2>
        {order.investigations.length === 0 ? (
          <p className="muted small">None yet.</p>
        ) : (
          <ul className="plain stack" style={{ gap: '0.4rem' }}>
            {order.investigations.map((run) => (
              <li key={run.id} className="row">
                <Status value={run.status} />
                <Link to={`/cases/${run.id}`}>{dateTime(run.created_at)}</Link>
                {run.incomplete_reason ? <span className="muted small">incomplete: {words(run.incomplete_reason)}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
