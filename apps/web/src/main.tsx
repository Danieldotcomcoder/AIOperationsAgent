import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import App from './app/App';
import { ApiError } from './api/client';
import { SessionProvider } from './lib/auth';
import './styles.css';

/**
 * Run state changes on the server (a worker finishes, a supervisor approves), so the
 * cache must not serve stale data confidently. Short staleness plus refetch on focus
 * keeps the approval inbox honest without a websocket.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: true,
      // A refusal (401/403/404/409/422) will not fix itself by being retried; a
      // network blip or a 5xx might.
      retry: (failureCount, error) => {
        const status = error instanceof ApiError ? error.status : 0;
        if (status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <SessionProvider>
          <App />
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
