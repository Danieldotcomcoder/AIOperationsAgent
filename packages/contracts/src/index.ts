/**
 * Shared API contract types.
 *
 * The Python Pydantic models are the source of truth. `generated.ts` is produced from
 * the API's OpenAPI schema (`pnpm contracts:generate`); the aliases below only give its
 * schemas short names. The few hand-written guards at the end are UI helpers over
 * those generated types, not copies of response shapes.
 *
 * Hand-copying a response type is how a frontend ends up confidently rendering a
 * field the backend stopped sending. `pnpm contracts:check` fails when the generated
 * file no longer matches the API.
 */

import type { components } from './generated';

export type { components, operations, paths } from './generated';

type Schema<Name extends keyof components['schemas']> = components['schemas'][Name];

export type Me = Schema<'MeResponse'>;
export type SystemInfo = Schema<'SystemInfo'>;
export type OrderSummary = Schema<'OrderSummary'>;
export type OrderDetail = Schema<'OrderDetail'>;
export type OrderCounts = Schema<'OrderCounts'>;
export type ShipmentEvent = Schema<'ShipmentEventView'>;
export type FollowupTask = Schema<'FollowupTaskView'>;
export type RunSummary = Schema<'RunSummary'>;
export type RunDetail = Schema<'RunDetail'>;
export type RunStep = Schema<'RunStepView'>;
export type RunResult = Schema<'RunResultView'>;
export type Finding = Schema<'Finding'>;
export type Validation = Schema<'ValidationView'>;
export type Feedback = Schema<'FeedbackView'>;
export type FeedbackRequest = Schema<'FeedbackRequest'>;
export type CreateRunRequest = Schema<'CreateRunRequest'>;
export type Action = Schema<'ActionView'>;
export type DecisionRequest = Schema<'DecisionRequest'>;
export type PolicyDocument = Schema<'DocumentView'>;
export type PolicyPassage = Schema<'PolicyPassageView'>;
export type MetricsSummary = Schema<'MetricsSummary'>;
export type DevTokenResponse = Schema<'DevTokenResponse'>;

export type RunStatus = Schema<'RunStatus'>;
export type IncompleteReason = Schema<'IncompleteReason'>;
export type ActionStatus = Schema<'ActionStatus'>;
export type Role = Schema<'Role'>;
export type Usefulness = Schema<'Usefulness'>;

/** Stable error codes from the backend. The UI branches on these, never on message text. */
export type ErrorCode =
  | 'auth.unauthenticated'
  | 'auth.forbidden'
  | 'resource.not_found'
  | 'state.conflict'
  | 'state.illegal_transition'
  | 'approval.stale'
  | 'approval.expired'
  | 'request.invalid'
  | 'budget.exhausted'
  | 'feature.writes_disabled'
  | 'provider.unavailable'
  | 'internal.error';

export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  'completed',
  'failed',
  'cancelled',
] as const;

export function isRunFinished(status: RunStatus): boolean {
  // needs_review is finished as far as the *run* is concerned: the worker is done and
  // the proposal is waiting on a human. Polling stops here.
  return TERMINAL_RUN_STATUSES.includes(status) || status === 'needs_review';
}

/** Terminal action states: nothing further will happen without a new investigation. */
export const SETTLED_ACTION_STATUSES: readonly ActionStatus[] = [
  'succeeded',
  'failed',
  'rejected',
  'expired',
] as const;

/**
 * A proposal is only actionable while pending, unexpired, intact and bound to
 * unchanged source state. The server enforces all of these; the UI must not offer a
 * button that the server will refuse.
 */
export function isApprovable(action: Action): boolean {
  return (
    action.status === 'pending' &&
    action.payload_valid &&
    !action.source_changed &&
    !action.decision_expired &&
    new Date(action.expires_at).getTime() > Date.now()
  );
}
