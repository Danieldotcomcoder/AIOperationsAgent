/**
 * Session state.
 *
 * In development the token comes from the local dev issuer (`POST /auth/dev/token`);
 * in staging and the pilot it will come from Auth0. The rest of the app only ever sees
 * `AuthContext`, so swapping the source changes this file and nothing else.
 *
 * The token is kept in memory and mirrored to sessionStorage so a page reload does not
 * log you out. Not localStorage: a token that outlives the tab is a token that outlives
 * the person walking away from the desk.
 *
 * Role is never read from the token. `/me` is the authority, because the server is the
 * only thing that can resolve a membership — and a claim the client can read is a claim
 * the client can misread.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { DevTokenResponse, Me } from '@opspilot/contracts';

import { request, UNAUTHORIZED_EVENT, type AuthContext as ApiAuth } from '../api/client';

const STORAGE_KEY = 'opspilot.session';
const ANONYMOUS: ApiAuth = { getToken: async () => null, organizationId: null };

export type { Me };

interface SessionState {
  token: string | null;
  me: Me | null;
  /** True while a stored token is being resolved into a membership. */
  restoring: boolean;
  loading: boolean;
  error: string | null;
  signIn: (email: string) => Promise<void>;
  signOut: () => void;
  /** Passed to every `request()` call. */
  api: ApiAuth;
}

const SessionContext = createContext<SessionState | null>(null);

function readStoredToken(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // Private browsing, or storage disabled. Sign-in still works for this tab.
    return null;
  }
}

function storeToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the session lives for this page only */
  }
}

function whoAmI(token: string): Promise<Me> {
  return request<Me>('/me', { getToken: async () => token, organizationId: null });
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Token and membership change together, so a screen never pairs one person's token
  // with another person's role.
  const [session, setSession] = useState<{ token: string | null; me: Me | null }>(() => ({
    token: readStoredToken(),
    me: null,
  }));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const drop = useCallback(
    (message: string | null) => {
      storeToken(null);
      setSession({ token: null, me: null });
      queryClient.clear();
      setError(message);
    },
    [queryClient],
  );

  // A token restored from storage still has to be resolved by the server.
  useEffect(() => {
    const token = session.token;
    if (!token || session.me) return;
    let cancelled = false;
    whoAmI(token)
      .then((me) => {
        if (!cancelled) setSession((current) => (current.token === token ? { token, me } : current));
      })
      .catch(() => {
        if (!cancelled) drop('Your session expired. Sign in again.');
      });
    return () => {
      cancelled = true;
    };
  }, [session.token, session.me, drop]);

  // Any authenticated call refused as unauthenticated ends the session cleanly.
  useEffect(() => {
    const onUnauthorized = () => drop('Your session expired. Sign in again.');
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [drop]);

  const signIn = useCallback(
    async (email: string) => {
      setLoading(true);
      setError(null);
      queryClient.clear();
      try {
        const issued = await request<DevTokenResponse>('/auth/dev/token', ANONYMOUS, {
          method: 'POST',
          body: JSON.stringify({ email }),
        });
        const me = await whoAmI(issued.access_token);
        storeToken(issued.access_token);
        setSession({ token: issued.access_token, me });
      } catch {
        setError('No active membership for that address.');
      } finally {
        setLoading(false);
      }
    },
    [queryClient],
  );

  const signOut = useCallback(() => drop(null), [drop]);

  const api = useMemo<ApiAuth>(
    () => ({
      getToken: async () => session.token,
      // The server re-resolves this against the caller's memberships, so it selects
      // among what they already have rather than granting anything.
      organizationId: session.me?.organization_id ?? null,
    }),
    [session],
  );

  const value = useMemo<SessionState>(
    () => ({
      token: session.token,
      me: session.me,
      restoring: Boolean(session.token && !session.me),
      loading,
      error,
      signIn,
      signOut,
      api,
    }),
    [session, loading, error, signIn, signOut, api],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession must be used inside SessionProvider');
  return ctx;
}
