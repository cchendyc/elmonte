import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Last line of defense for data-driven rendering.
 *
 * GraphQL responses are typed, but values still originate in a pipeline and a
 * single malformed record (an invalid currency code, an unexpected enum) used
 * to take down the whole SPA. If an unexpected render error escapes a route,
 * show a recoverable message instead of a blank white page.
 */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) {
      console.error("[AppErrorBoundary]", error, info.componentStack);
    }
  }

  render() {
    if (this.state.error) {
      return (
        <main className="page-placeholder" role="alert">
          <h1>Something went wrong</h1>
          <p>An unexpected error occurred while rendering this page.</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              this.setState({ error: null });
              window.location.assign("/");
            }}
          >
            Reload the atlas
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
