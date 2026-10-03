/**
 * Start an investigation.
 *
 * Deliberately plain: one question, optionally scoped to an order. The order number is
 * sent as its own field rather than being parsed out of the question, so the backend
 * can resolve and bind the target record itself — a proposal has to bind to a real
 * order id and its version, and neither may come from free text the model saw.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { CreateRunRequest, RunSummary } from '@opspilot/contracts';

import { ApiError, request } from '../../api/client';
import { Banner } from '../../components/Status';
import { useSession } from '../../lib/auth';

/** The request types the product plan supports (docs/plan/00-project-plan.md). */
const SUGGESTIONS = [
  'Why is this order delayed, and what should we do?',
  'Explain this shipment exception using our policy.',
  'Which orders are overdue today?',
];

const MESSAGES: Record<string, string> = {
  'budget.exhausted': 'The spend or run limit has been reached. No investigation was started.',
  'feature.writes_disabled': 'Investigations are currently disabled by an operator.',
  'request.invalid': 'Check the order number and question, then try again.',
  'client.network': 'The API could not be reached. Is it running?',
};

export default function NewInvestigation({ orderNumber }: { orderNumber?: string }) {
  const { api } = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [order, setOrder] = useState(orderNumber ?? '');
  const [question, setQuestion] = useState(SUGGESTIONS[0] ?? '');
  const [message, setMessage] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      const number = order.trim().toUpperCase();
      const body: CreateRunRequest = {
        request_text: number ? `Investigate order ${number}. ${question.trim()}` : question.trim(),
        order_number: number || null,
      };
      return request<RunSummary>('/runs', api, { method: 'POST', body: JSON.stringify(body) });
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
      navigate(`/cases/${run.id}`);
    },
    onError: (error) => {
      // Budget exhaustion is an expected, explainable state, not a crash. Say which.
      const code = error instanceof ApiError ? error.code : 'internal.error';
      setMessage(MESSAGES[code] ?? 'Could not start the investigation.');
    },
  });

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        setMessage(null);
        mutation.mutate();
      }}
    >
      <div className="new-run">
        {orderNumber ? null : (
          <input
            className="order mono"
            value={order}
            onChange={(event) => setOrder(event.target.value)}
            placeholder="Order, e.g. SO-1042"
            aria-label="Order number (optional)"
            maxLength={32}
            pattern="[A-Za-z0-9_\-]*"
          />
        )}
        <input
          className="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          aria-label="Question"
          list="investigation-suggestions"
          minLength={3}
          maxLength={900}
          required
        />
        <datalist id="investigation-suggestions">
          {SUGGESTIONS.map((suggestion) => (
            <option key={suggestion} value={suggestion} />
          ))}
        </datalist>
        <button type="submit" className="primary" disabled={mutation.isPending}>
          {mutation.isPending ? 'Starting…' : orderNumber ? `Investigate ${orderNumber}` : 'Investigate'}
        </button>
      </div>
      {message ? <Banner tone="critical">{message}</Banner> : null}
    </form>
  );
}
