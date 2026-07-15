import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import App from "./App";
import { AuthProvider } from "./auth";
import { ConfirmProvider } from "./components/confirm";
import ErrorBoundary from "./components/ErrorBoundary";
import "@xyflow/react/dist/style.css";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Never retry non-transient 4xx — a 404/403/422 won't heal, and the retry
      // window is what let the offline-pause trap freeze pages on a spinner
      // (UX benchmark P2: /datasets/<bad-id> spun forever; Lineage rendered a
      // blank pane). 408/429 are transient by definition and keep one retry.
      retry: (failureCount, error) => {
        const status = error instanceof ApiError ? error.status : null;
        const permanent4xx =
          status !== null && status >= 400 && status < 500 && status !== 408 && status !== 429;
        return !permanent4xx && failureCount < 1;
      },
      refetchOnWindowFocus: false,
      staleTime: 15_000,
      // The API is same-origin: don't let onlineManager silently PAUSE fetches
      // when the browser thinks it's offline — a paused query reports neither
      // loading nor error, which the pages render as an eternal spinner or a
      // blank pane. Failing fast surfaces an honest, retryable error instead.
      networkMode: "always",
      // "always" flips TanStack's refetchOnReconnect default to false — restore
      // it so error screens self-heal when connectivity returns (codex review).
      refetchOnReconnect: true,
      // Mutations deliberately KEEP the default "online" mode: pausing an
      // offline write and resuming on reconnect beats an instant failure that
      // several call sites would render silently (codex review).
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ConfirmProvider>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ConfirmProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
