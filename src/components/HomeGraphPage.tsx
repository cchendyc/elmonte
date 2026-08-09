import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  InvestigationTrace,
  type CanvasViewMode,
} from "./InvestigationTrace";
import { OrgChart } from "./OrgChart";
import { PeopleScatter } from "./PeopleScatter";
import { PersonDetailPanel } from "./PersonDetailPanel";
import { useGraphSession } from "../lib/useGraphSession";

const CANVAS_VIEW_STORAGE_KEY = "elmonte-canvas-view-mode";
const LEGACY_TRACE_VIEW_STORAGE_KEY = "elmonte-trace-view-mode";

function isPersonId(id: string | null): id is string {
  return id != null && id.startsWith("p:");
}

function readViewMode(): CanvasViewMode {
  try {
    const stored =
      localStorage.getItem(CANVAS_VIEW_STORAGE_KEY) ??
      localStorage.getItem(LEGACY_TRACE_VIEW_STORAGE_KEY);
    if (stored === "graph") return "graph";
    return "scatter";
  } catch {
    return "scatter";
  }
}

export function HomeGraphPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get("focus");
  const setFocus = (id: string) => setSearchParams({ focus: id });
  const [profileOpen, setProfileOpen] = useState(false);
  const [viewMode, setViewMode] = useState<CanvasViewMode>(readViewMode);

  const graphSession = useGraphSession(focusId ?? "", setFocus);

  const showProfile = isPersonId(focusId) && profileOpen;

  useEffect(() => {
    if (isPersonId(focusId)) {
      setProfileOpen(true);
    } else {
      setProfileOpen(false);
    }
  }, [focusId]);

  function handleViewModeChange(mode: CanvasViewMode) {
    setViewMode(mode);
    try {
      localStorage.setItem(CANVAS_VIEW_STORAGE_KEY, mode);
    } catch {
      // Ignore storage failures
    }
  }

  return (
    <div
      className={`canvas-split${showProfile ? " canvas-split--profile-open" : ""}`}
    >
      <div
        className="canvas-split__pane canvas-split__pane--tree"
        data-tour="org-chart"
      >
        <div className="investigation-panel">
          <InvestigationTrace
            session={graphSession.session}
            api={graphSession}
            onFocus={setFocus}
            viewMode={viewMode}
            onViewModeChange={handleViewModeChange}
            error={graphSession.error}
          />
        </div>
      </div>

      <div
        className="canvas-split__pane canvas-split__pane--main"
        data-tour={viewMode === "scatter" ? "scatter" : "graph"}
      >
        {viewMode === "scatter" ? (
          <PeopleScatter
            focusId={focusId}
            onFocus={setFocus}
            minHeight="100%"
          />
        ) : focusId ? (
          <OrgChart
            focusId={focusId}
            session={graphSession.session}
            api={graphSession}
            error={graphSession.error}
            minHeight="100%"
          />
        ) : (
          <div className="empty-state empty-state--canvas">
            <div className="empty-state__icon" aria-hidden="true">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.75" />
                <path
                  d="M12 2V5M12 19V22M2 12H5M19 12H22M4.9 4.9L7 7M17 17L19.1 19.1M4.9 19.1L7 17M17 7L19.1 4.9"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                />
              </svg>
            </div>
            <h2>Relationship graph</h2>
            <p>
              Pick a focus from the trace on the left, or search for a
              researcher or institution to explore their network.
            </p>
          </div>
        )}
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
