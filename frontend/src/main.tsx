import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
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
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 },
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
