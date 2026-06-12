import { Component, type ErrorInfo, type ReactNode } from "react";

/** Catches render-time exceptions so a bad payload (one chart, one page) shows
 *  an error card instead of unmounting the entire app to a blank screen.
 *  Give it a `key` (e.g. the route path) to auto-reset on navigation. */
export default class ErrorBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Render error caught by boundary:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return (
      <div className="card card-pad" style={{ margin: 16 }}>
        <h3>Something went wrong displaying this view</h3>
        <div className="error-box">{this.state.error.message || String(this.state.error)}</div>
        <button className="small" style={{ marginTop: 8 }} onClick={() => this.setState({ error: null })}>
          Try again
        </button>
      </div>
    );
  }
}
