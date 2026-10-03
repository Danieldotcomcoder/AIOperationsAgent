/**
 * Operations inbox — overdue orders and exception cases.
 *
 * The first screen, and the one an analyst lives in. It shows what the server says and
 * nothing more: no client-side recomputation of "overdue", because the agent answers
 * that question from `services/orders.py` on the same business date, and the two must
 * not be able to disagree.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { OrderCounts, OrderSummary, RunSummary } from '@opspilot/contracts';

import { describeError, request } from '../../api/client';
import { Banner } from '../../components/Status';
import { useSession } from '../../lib/auth';
import { day, money, words } from '../../lib/format';
import NewInvestigation from '../runs/NewInvestigation';

const PAGE = 25;

function QuickInvestigate({ order }: { order: OrderSummary }) {
  const { api } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: () =>
      request<RunSummary>('/runs', api, {
        method: 'POST',
        body: JSON.stringify({
          request_text: `Investigate order ${order.order_number}. Why is this order delayed, and what should we do?`,
          order_number: order.order_number,
        }),
      }),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
      navigate(`/cases/${run.id}`);
    },
  });
  return (
    <button
      type="button"
      disabled={start.isPending}
      title={start.error ? describeError(start.error) : `Investigate ${order.order_number}`}
      onClick={(event) => {
        event.stopPropagation();
        start.mutate();
      }}
    >
      {start.isPending ? 'Starting…' : start.isError ? 'Retry' : 'Investigate'}
    </button>
  );
}

export default function OrdersInbox() {
  const { api, me } = useSession();
  const navigate = useNavigate();
  const [overdueOnly, setOverdueOnly] = useState(true);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [page, setPage] = useState(0);

  // Search as you type, without a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => setPage(0), [overdueOnly, flaggedOnly, term]);

  const params = new URLSearchParams({
    overdue_only: String(overdueOnly),
    flagged_only: String(flaggedOnly),
    limit: String(PAGE + 1),
    offset: String(page * PAGE),
  });
  if (term) params.set('q', term);

  const orders = useQuery({
    queryKey: ['orders', me?.organization_id, overdueOnly, flaggedOnly, term, page],
    queryFn: () => request<OrderSummary[]>(`/orders?${params.toString()}`, api),
    placeholderData: keepPreviousData,
  });
  const counts = useQuery({
    queryKey: ['order-counts', me?.organization_id],
    queryFn: () => request<OrderCounts>('/orders/counts', api),
  });

  const rows = (orders.data ?? []).slice(0, PAGE);
  const hasMore = (orders.data?.length ?? 0) > PAGE;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Operations inbox</h1>
          <p>
            {counts.data
              ? `${counts.data.overdue} of ${counts.data.total} orders are overdue as of ${day(counts.data.as_of)}.`
              : 'Overdue orders and carrier exceptions.'}
          </p>
        </div>
      </div>

      <section className="card">
        <h2>Ask about an order</h2>
        <p className="muted small">
          The agent reads the order, its shipments and carrier events, and the policies in
          force, then proposes a follow-up task for a supervisor to approve. Nothing is
          written until then.
        </p>
        <NewInvestigation />
      </section>

      <section className="card flush">
        <div className="filters" style={{ padding: '0.9rem 1.1rem 0' }}>
          <label className="check">
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(event) => setOverdueOnly(event.target.checked)}
            />
            Overdue only
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={flaggedOnly}
              onChange={(event) => setFlaggedOnly(event.target.checked)}
            />
            Carrier reported a problem
          </label>
          <input
            type="search"
            placeholder="Search order or customer"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search orders"
            maxLength={64}
          />
        </div>
        {orders.error ? (
          <div style={{ padding: '0 1.1rem' }}>
            <Banner tone="critical">Could not load orders. {describeError(orders.error)}</Banner>
          </div>
        ) : null}
        {orders.isPending ? (
          <p className="empty">Loading orders…</p>
        ) : rows.length === 0 ? (
          <p className="empty">No orders match. That is the good outcome.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Customer</th>
                  <th>Status</th>
                  <th>Promised</th>
                  <th className="num">Overdue</th>
                  <th className="num">Value</th>
                  <th>Carrier</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map((order) => (
                  <tr
                    key={order.id}
                    className="clickable"
                    onClick={() => navigate(`/orders/${order.id}`)}
                  >
                    {/* Text, never markup: every string on this page is untrusted until
                        proven otherwise, and React escaping is the control. */}
                    <td className="mono">
                      <a href={`/orders/${order.id}`} onClick={(event) => event.preventDefault()}>
                        {order.order_number}
                      </a>
                    </td>
                    <td>{order.customer_name}</td>
                    <td>{words(order.status)}</td>
                    <td className="nowrap">{day(order.promised_date)}</td>
                    <td className="num">{order.days_overdue > 0 ? `${order.days_overdue} d` : '—'}</td>
                    <td className="num">{money(order.total_amount)}</td>
                    <td>
                      {order.has_open_exception ? (
                        <span className="status warning" title="A carrier reported a delay or exception">
                          <span className="icon" aria-hidden="true">
                            !
                          </span>
                          reported issue
                        </span>
                      ) : null}
                    </td>
                    <td className="num">
                      <QuickInvestigate order={order} />
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
          <button type="button" disabled={!hasMore} onClick={() => setPage((p) => p + 1)}>
            Next
          </button>
        </div>
      </section>
    </div>
  );
}
