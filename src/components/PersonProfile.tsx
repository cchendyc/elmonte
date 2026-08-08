import { Link } from "react-router-dom";

interface Props {
  personId: string;
}

/**
 * Placeholder route. The old PersonProfile relied on the retired seed graph
 * and its aggregations (career timeline, coauthorships, action modals). The
 * real replacement will need its own endpoints (single-person profile,
 * publication list, affiliation history) and a UI redesign against the new
 * schema. Until that's done, we route the user back to the graph — the
 * accumulating chart is the primary exploration surface anyway.
 */
export function PersonProfile({ personId }: Props) {
  return (
    <div className="page-placeholder">
      <div className="page-placeholder__card">
        <div className="page-placeholder__icon" aria-hidden="true">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.5" />
            <path
              d="M5 20C5 16.134 8.13401 13 12 13C15.866 13 19 16.134 19 20"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </div>
        <span className="record-type">Person profile</span>
        <h1>Full profile page in progress</h1>
        <p>
          The dedicated profile view is being rebuilt against the new database
          schema. Open this person on the graph to explore their relationships,
          timeline, and publications.
        </p>
        <Link className="button-primary" to={`/?focus=${personId}`}>
          Open in graph
        </Link>
      </div>
    </div>
  );
}
