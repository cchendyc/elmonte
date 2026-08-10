import { useMemo } from "react";
import type { SessionNode, SessionState } from "../lib/graphSession";
import {
  buildTopDownTrace,
  nextTraceOptions,
  resolveNode,
  stepKindLabel,
  stepMeta,
} from "../lib/tracePath";
import type { TraceCatalog } from "../lib/traceCatalog";
import { useTraceCatalog } from "../lib/useTraceCatalog";
import type { GraphSessionApi } from "../lib/useGraphSession";

export type CanvasViewMode = "graph" | "scatter";

interface Props {
  session: SessionState;
  api: Pick<GraphSessionApi, "expand" | "onTraceSelect">;
  onFocus: (id: string) => void;
  viewMode: CanvasViewMode;
  onViewModeChange: (mode: CanvasViewMode) => void;
  error?: string | null;
}

function nodeLabel(
  node: SessionNode | { label: string } | undefined,
  id: string,
): string {
  return node?.label ?? id;
}

function TraceIcon({
  node,
}: {
  node: SessionNode | { orgKind?: string | null; kind?: string } | undefined;
}) {
  const kind =
    node && "kind" in node && node.kind === "person"
      ? "person"
      : node && "orgKind" in node
        ? (node.orgKind ?? "org")
        : "org";

  return (
    <span className={`trace-card__icon trace-card__icon--${kind}`} aria-hidden="true">
      {kind === "university" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path
            d="M12 3L20 8V16L12 21L4 16V8L12 3Z"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinejoin="round"
          />
          <path d="M4 8H20M8 8V16M16 8V16" stroke="currentColor" strokeWidth="1.75" />
        </svg>
      )}
      {kind === "school" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path
            d="M4 20V9L12 4L20 9V20"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinejoin="round"
          />
          <path d="M9 20V13H15V20" stroke="currentColor" strokeWidth="1.75" />
        </svg>
      )}
      {(kind === "department" || kind === "lab") && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path
            d="M12 3L14.5 8.5L20.5 9.3L16.2 13.4L17.2 19.4L12 16.7L6.8 19.4L7.8 13.4L3.5 9.3L9.5 8.5L12 3Z"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinejoin="round"
          />
        </svg>
      )}
      {kind === "person" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="8" r="3.5" stroke="currentColor" strokeWidth="1.75" />
          <path
            d="M5.5 20C6.4 16.8 8.8 15 12 15C15.2 15 17.6 16.8 18.5 20"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
          />
        </svg>
      )}
      {kind !== "university" &&
        kind !== "school" &&
        kind !== "department" &&
        kind !== "lab" &&
        kind !== "person" && (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <rect
              x="5"
              y="5"
              width="14"
              height="14"
              rx="2"
              stroke="currentColor"
              strokeWidth="1.75"
            />
          </svg>
        )}
    </span>
  );
}

function ChevronRight() {
  return (
    <svg
      className="trace-card__chevron"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M9 6L15 12L9 18"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface TraceCardProps {
  node: SessionNode | { label: string; orgKind?: string | null; sublabel?: string | null } | undefined;
  id: string;
  meta: string;
  value: string;
  options: string[];
  catalog: TraceCatalog;
  session: SessionState;
  onChange: (id: string) => void;
  placeholder?: string;
  active?: boolean;
  pending?: boolean;
}

function TraceCard({
  node,
  id,
  meta,
  value,
  options,
  catalog,
  session,
  onChange,
  placeholder,
  active,
  pending,
}: TraceCardProps) {
  return (
    <label
      className={`trace-card${active ? " trace-card--active" : ""}${pending ? " trace-card--next" : ""}${value ? "" : " trace-card--empty"}`}
    >
      <TraceIcon node={node} />
      <span className="trace-card__body">
        <span className="trace-card__title">
          {value ? nodeLabel(node, id) : placeholder ?? "Select…"}
        </span>
        {value && meta && <span className="trace-card__meta">{meta}</span>}
        {!value && pending && placeholder && (
          <span className="trace-card__meta">{placeholder}</span>
        )}
      </span>
      <ChevronRight />
      <select
        className="trace-card__select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{placeholder ?? "Select…"}</option>
        {options.map((optionId) => {
          const option = resolveNode(session, catalog, optionId);
          return (
            <option key={optionId} value={optionId}>
              {nodeLabel(option, optionId)}
              {option ? ` · ${stepMeta(option, session, optionId)}` : ""}
            </option>
          );
        })}
      </select>
    </label>
  );
}

export function InvestigationTrace({
  session,
  api,
  onFocus,
  viewMode,
  onViewModeChange,
  error,
}: Props) {
  const { catalog, loading: catalogLoading } = useTraceCatalog(session);

  const steps = useMemo(
    () => buildTopDownTrace(session, catalog),
    [session, catalog],
  );

  const nextOptions = useMemo(
    () => nextTraceOptions(session, catalog),
    [session, catalog],
  );

  const anyLoading =
    catalogLoading || Object.values(session.loading).some(Boolean);

  function handleSelect(id: string) {
    if (!id) return;
    onFocus(id);
    api.onTraceSelect(id);
  }

  return (
    <div className="investigation-trace" data-testid="investigation-trace">
      <div className="investigation-trace__body">
        <section className="trace-hierarchy" aria-label="Investigation path">
          <ol className="trace-hierarchy__list">
            {steps.map((step) => {
              const node = step.id
                ? resolveNode(session, catalog, step.id)
                : undefined;
              const isFocus = step.id === session.focusId;
              return (
                <li
                  key={`step-${step.index}-${step.id || "pending"}`}
                  className="trace-hierarchy__item"
                >
                  <TraceCard
                    node={node}
                    id={step.id}
                    meta={step.id ? stepMeta(node, session, step.id) : ""}
                    value={step.pending ? "" : step.id}
                    options={step.options}
                    catalog={catalog}
                    session={session}
                    pending={step.pending}
                    placeholder={step.placeholder}
                    active={isFocus}
                    onChange={handleSelect}
                  />
                </li>
              );
            })}

            {steps.length > 0 &&
              !steps.some((step) => step.pending) &&
              nextOptions.length > 0 && (
                <li className="trace-hierarchy__item trace-hierarchy__item--next">
                  <label className="trace-card trace-card--next">
                    <span
                      className="trace-card__icon trace-card__icon--next"
                      aria-hidden="true"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M12 5V19M5 12H19"
                          stroke="currentColor"
                          strokeWidth="1.75"
                          strokeLinecap="round"
                        />
                      </svg>
                    </span>
                    <span className="trace-card__body">
                      <span className="trace-card__title">
                        {anyLoading ? "Loading…" : "Continue down"}
                      </span>
                      <span className="trace-card__meta">
                        Researcher or nested unit
                      </span>
                    </span>
                    <ChevronRight />
                    <select
                      className="trace-card__select"
                      value=""
                      disabled={anyLoading}
                      onChange={(event) => handleSelect(event.target.value)}
                    >
                      <option value="">Select…</option>
                      {nextOptions.map((optionId) => {
                        const option = resolveNode(session, catalog, optionId);
                        return (
                          <option key={optionId} value={optionId}>
                            {nodeLabel(option, optionId)}
                            {option ? ` · ${stepKindLabel(option)}` : ""}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                </li>
              )}
          </ol>
        </section>

        <footer className="investigation-trace__footer">
          <label className="investigation-trace__view">
            <span>View</span>
            <select
              value={viewMode}
              onChange={(event) =>
                onViewModeChange(event.target.value as CanvasViewMode)
              }
              aria-label="Main canvas view"
            >
              <option value="scatter">Scatter</option>
              <option value="graph">Graph</option>
            </select>
          </label>
          <div className="investigation-trace__status">
            {anyLoading ? (
              <span className="investigation-trace__status-dot investigation-trace__status-dot--loading" />
            ) : (
              <span className="investigation-trace__status-dot" />
            )}
            {anyLoading ? "Loading…" : `${steps.filter((s) => s.id).length} levels`}
          </div>
          {error && (
            <div className="investigation-trace__error-banner" role="alert">
              <strong>Network unavailable</strong>
              <span>{error}</span>
            </div>
          )}
        </footer>
      </div>
    </div>
  );
}
