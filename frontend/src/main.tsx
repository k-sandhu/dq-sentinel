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
      // Never retry 4xx — a 404/403 won't heal, and the retry window is what
      // let the offline-pause trap freeze pages on a spinner (UX benchmark P2:
      // /datasets/<bad-id> spun forever; Lineage rendered a blank pane).
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status >= 400 && error.status < 500) &&
        failureCount < 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
      // The API is same-origin: don't let onlineManager silently PAUSE fetches
      // when the browser thinks it's offline — a paused query reports neither
      // loading nor error, which the pages render as an eternal spinner or a
      // blank pane. Failing fast surfaces an honest, retryable error instead.
      networkMode: "always",
    },
    mutations: { networkMode: "always" },
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
