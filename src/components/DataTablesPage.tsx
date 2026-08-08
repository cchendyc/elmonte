import { Link } from "react-router-dom";

/**
 * The old data tables page mirrored the in-memory seed graph. It was a
 * useful diagnostic while the frontend was standalone, but now that the DB
 * is the source of truth it needs a completely different pipeline (paginated
 * SQL queries against each table). Left as a placeholder until those
 * endpoints exist — a full-fidelity table browser is not part of the graph
 * scope shipped in this iteration.
 */
export function DataTablesPage() {
  return (
    <div className="page-placeholder">
      <div className="page-placeholder__card">
        <div className="page-placeholder__icon" aria-hidden="true">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
            <rect
              x="3"
              y="3"
              width="18"
              height="18"
              rx="3"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <path
              d="M3 9H21M3 15H21M9 3V21"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
        </div>
        <span className="record-type">Data tables</span>
        <h1>Table browser coming soon</h1>
        <p>
          A paginated view over the Postgres tables is in development. For now,
          explore researchers and institutions through the interactive graph.
        </p>
        <Link className="button-primary" to="/">
          Open graph
        </Link>
      </div>
    </div>
  );
}
