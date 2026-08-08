import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { OrgChart } from "./OrgChart";
import { PeopleScatter } from "./PeopleScatter";
import { PersonDetailPanel } from "./PersonDetailPanel";

function isPersonId(id: string | null): id is string {
  return id != null && id.startsWith("p:");
}

export function HomeGraphPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const setFocus = (id: string) => setSearchParams({ focus: id });
  const [profileOpen, setProfileOpen] = useState(false);

  const showProfile = isPersonId(focusId) && profileOpen;

  useEffect(() => {
    if (isPersonId(focusId)) {
      setProfileOpen(true);
    } else {
      setProfileOpen(false);
    }
  }, [focusId]);

  return (
    <div
      className={`canvas-split${showProfile ? " canvas-split--profile-open" : ""}`}
    >
      <div
        className="canvas-split__pane canvas-split__pane--tree"
        data-tour="org-chart"
      >
        {focusId ? (
          <OrgChart focusId={focusId} onFocus={setFocus} minHeight="100%" />
        ) : (
          <div className="empty-state empty-state--canvas">
            <div className="empty-state__icon" aria-hidden="true">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none">
                <path
                  d="M4 19V5C4 4.44772 4.44772 4 5 4H19C19.5523 4 20 4.44772 20 5V19"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
                <path
                  d="M4 9H20M9 4V9M15 4V9"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
                <circle cx="8" cy="14" r="1.5" fill="currentColor" />
                <circle cx="12" cy="16" r="1.5" fill="currentColor" />
                <circle cx="16" cy="13" r="1.5" fill="currentColor" />
              </svg>
            </div>
            <h2>Start exploring</h2>
            <p>
              Search for a researcher or institution above, or select a point
              on the people map. Each selection expands the relationship graph
              from the database.
            </p>
            <ul className="empty-state__hints">
              <li>Use the search bar to jump to a person or org</li>
              <li>Click nodes on the map to build your investigation</li>
              <li>Open profiles to view career timelines and papers</li>
            </ul>
          </div>
        )}
      </div>

      <div className="canvas-split__pane canvas-split__pane--scatter" data-tour="scatter">
        <PeopleScatter
          focusId={focusId}
          onFocus={setFocus}
          minHeight="100%"
        />
      </div>

      {showProfile && (
        <div className="canvas-split__pane canvas-split__pane--profile">
          <PersonDetailPanel
            personId={focusId}
            onFocusPerson={setFocus}
            onClose={() => setProfileOpen(false)}
          />
        </div>
      )}

      {isPersonId(focusId) && !profileOpen && (
        <button
          type="button"
          className="person-profile__reopen"
          onClick={() => setProfileOpen(true)}
        >
          Profile
        </button>
      )}
    </div>
  );
}
