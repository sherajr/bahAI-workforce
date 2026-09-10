import { Component, Suspense } from "react";
import type { ErrorInfo, ReactNode } from "react";

/**
 * One panel failing must not take the whole dashboard with it (rule 112).
 *
 * Every tab is loaded on demand now, so there are two new ways a panel can fail
 * that did not exist when everything was in one bundle: the chunk request can
 * fail (a dev server restarted, a stale index after a redeploy, no network),
 * and the panel's own render can throw before the shell is drawn. Both used to
 * be impossible; both are now a blank screen unless something catches them.
 *
 * Sheraj is non-technical and the deliverable is dashboard-visible behaviour
 * (AGENTS.md), so a failure here says what happened and offers the one action
 * that fixes the common case, rather than logging to a console nobody opens.
 */

type Props = { name: string; children: ReactNode };
type State = { error: Error | null };

class Boundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept out of the UI but not swallowed: a stack is what makes this
    // diagnosable at all, and it is the one place it exists.
    console.error(`[${this.props.name}] panel failed`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    const chunkFailure = /dynamically imported module|Loading chunk|Importing a module script/i
      .test(this.state.error.message || "");
    return (
      <div style={{ padding: 24, maxWidth: 640 }}>
        <h2 style={{ margin: "0 0 8px", fontSize: 18 }}>
          The {this.props.name} panel could not open.
        </h2>
        <p style={{ margin: "0 0 12px", opacity: 0.85, lineHeight: 1.5 }}>
          {chunkFailure
            ? "Its code could not be downloaded. This usually means the app was rebuilt " +
              "while the page was open, or the dev server restarted."
            : "Something in this panel threw an error while it was drawing."}{" "}
          Everything else in the dashboard is still working, and nothing was lost.
        </p>
        <pre
          style={{
            background: "rgba(127,127,127,.12)", padding: 10, borderRadius: 6,
            fontSize: 12, overflowX: "auto", margin: "0 0 12px",
          }}
        >
          {this.state.error.message}
        </pre>
        <button onClick={() => window.location.reload()}>Reload the dashboard</button>
      </div>
    );
  }
}

function Loading({ name }: { name: string }) {
  return (
    <div style={{ padding: 24, opacity: 0.7 }} role="status" aria-live="polite">
      Opening {name}…
    </div>
  );
}

/** A lazily-loaded panel, with both of its failure modes covered. */
export function PanelBoundary({ name, children }: Props) {
  return (
    <Boundary name={name}>
      <Suspense fallback={<Loading name={name} />}>{children}</Suspense>
    </Boundary>
  );
}
