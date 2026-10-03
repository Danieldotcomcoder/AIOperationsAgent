import { useQuery } from '@tanstack/react-query';
import type { SystemInfo } from '@opspilot/contracts';

import { request } from '../api/client';
import { useSession } from './auth';

/** What this deployment runs: model, business date, bounds. Changes only on restart. */
export function useSystem() {
  const { api, me } = useSession();
  return useQuery({
    queryKey: ['system', me?.organization_id],
    queryFn: () => request<SystemInfo>('/system', api),
    enabled: Boolean(me),
    staleTime: 5 * 60_000,
  });
}
