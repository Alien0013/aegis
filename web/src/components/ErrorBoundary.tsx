// Catches render errors in a page so one broken page shows a message instead of
// blanking the entire dashboard. Resets when the route (key) changes.

import { Component, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="rounded-[calc(var(--radius)+2px)] border border-danger/40 bg-danger/10 p-6 text-sm">
          <div className="mb-1 font-semibold text-danger">This page hit an error</div>
          <div className="mb-3 text-dim">The rest of the dashboard is fine — try another page or reload.</div>
          <pre className="scroll-thin max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-[var(--radius)] border border-border bg-surface-2 p-3 font-mono text-xs text-faint">
            {String(this.state.error?.stack || this.state.error)}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
