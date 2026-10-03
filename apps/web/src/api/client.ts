/**
 * The single HTTP boundary.
 *
 * Every call goes through `request()`. That is what makes three things true in one
 * place instead of forty: the bearer token is attached, the active organization
 * header is sent, and a typed `ApiError` carrying the backend's stable `error_code`
 * is thrown on failure.
 *
 * The organization header selects among the caller's *own* memberships. The server
 * re-resolves it against the membership table and rejects anything else — the client
 * cannot widen its own access by editing it.
 */

import type { ErrorCode } from '@opspilot/contracts';

export interface ApiErrorBody {
  error_code: string;
  message: string;
}

/** Fired when an authenticated call is refused as unauthenticated (expired token). */
export const UNAUTHORIZED_EVENT = 'opspilot:unauthorized';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: ErrorCode | string,
    message: string,
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** A stale approval needs a different UI response than a permissions failure. */
  get isStaleApproval(): boolean {
    return this.code === 'approval.stale' || this.code === 'approval.expired';
  }

  get isForbidden(): boolean {
    return this.code === 'auth.forbidden';
  }
}

export interface AuthContext {
  /** Access token from the OIDC provider, or the local dev issuer. */
  getToken: () => Promise<string | null>;
  organizationId: string | null;
}

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '/api';

export async function request<T>(
  path: string,
  auth: AuthContext,
  init: RequestInit = {},
): Promise<T> {
  const token = await auth.getToken();
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (auth.organizationId) headers.set('X-Organization', auth.organizationId);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, 'client.network', 'The API could not be reached. Is it running?', null);
  }
  const requestId = response.headers.get('X-Request-Id');

  if (!response.ok) {
    let body: ApiErrorBody = { error_code: 'internal.error', message: response.statusText };
    try {
      const parsed = await response.json();
      body = parsed.error_code
        ? parsed
        : { error_code: 'request.invalid', message: 'Check the supplied fields and try again.' };
    } catch {
      // A non-JSON error body is itself information; keep the status-text fallback.
    }
    if (response.status === 401 && token) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(response.status, body.error_code, body.message, requestId);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** A user-facing sentence for a failed call, with the request id for support. */
export function describeError(error: unknown, fallback = 'The request failed.'): string {
  if (!(error instanceof ApiError)) return fallback;
  const reference = error.requestId ? ` (reference ${error.requestId.slice(0, 8)})` : '';
  return `${error.message || fallback}${reference}`;
}
