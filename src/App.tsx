import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  useLocation,
  Navigate,
  Route,
  Routes,
  useSearchParams,
  useParams,
} from "react-router-dom";
import { useQuery } from "@apollo/client/react";
import { ExploreGraphPage } from "./components/ExploreGraphPage";
import { DataTablesPage } from "./components/DataTablesPage";
import { HomeGraphPage } from "./components/HomeGraphPage";
import { InstitutionPage } from "./components/InstitutionPage";
import { PersonProfile } from "./components/PersonProfile";
import { SEARCH, type SearchData, type SearchVars } from "./api/queries";
import {
  OnboardingProvider,
  useOnboarding,
} from "./components/OnboardingTour";
import "./App.css";

function PersonRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to="/" replace />;
  return <PersonProfile personId={id} />;
}

function InstitutionRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return <Navigate to="/" replace />;
  return <InstitutionPage institutionId={id} />;
}

/** Debounce a value by `delay` ms. */
function useDebounced<T>(value: T, delay = 220): T {
  const [d, setD] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setD(value), delay);
    return () => window.clearTimeout(t);
  }, [value, delay]);
  return d;
}

function SearchIcon() {
  return (
    <svg
      className="search-icon"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path
        d="M20 20L16.5 16.5"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function AppHeader() {
  const location = useLocation();
  const { startTour } = useOnboarding();
  const [, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 220);
  const showDirectorySearch = location.pathname === "/";
  const isDataPage = location.pathname === "/data";

  const { data } = useQuery<SearchData, SearchVars>(SEARCH, {
    variables: { q: debounced, limit: 10 },
    skip: debounced.length < 1,
    fetchPolicy: "cache-first",
  });

  const peopleMatches = data?.search.people ?? [];
  const orgMatches = data?.search.orgs ?? [];

  function focusResult(id: string) {
    setSearchParams({ focus: id });
    setQuery("");
  }

  function applyFirstResult() {
    const id = peopleMatches[0]?.id ?? orgMatches[0]?.id;
    if (id) focusResult(id);
  }

  return (
    <header className="site-header">
      <div className="site-header__start">
        <Link
          className="site-brand"
          to="/"
          aria-label="El Monte research atlas home"
          data-tour="brand"
        >
          <span className="site-brand__mark" aria-hidden="true">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 2L20 8.5V15.5L12 22L4 15.5V8.5L12 2Z"
                stroke="currentColor"
                strokeWidth="1.75"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="2.5" fill="currentColor" />
            </svg>
          </span>
          <span className="site-brand__text">
            <strong>El Monte</strong>
            <small>Research Atlas</small>
          </span>
        </Link>
      </div>

      {showDirectorySearch && (
        <div className="site-header__center" data-tour="search">
          <div className="site-header__search org-chart-search">
            <label className="hero-search__field">
              <span className="sr-only">
                Search people and organization units
              </span>
              <SearchIcon />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") applyFirstResult();
                }}
                placeholder="Search researchers, institutions, labs…"
                autoComplete="off"
              />
              {query && (
                <button
                  type="button"
                  className="hero-search__clear"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M6 6L18 18M18 6L6 18"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                    />
                  </svg>
                </button>
              )}
            </label>

            {debounced && (
              <div className="search-results" role="listbox">
                {peopleMatches.length > 0 && (
                  <p className="search-results__label">People</p>
                )}
                {peopleMatches.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    className="search-result"
                    onClick={() => focusResult(hit.id)}
                  >
                    <span className="search-result__avatar search-result__avatar--person">
                      {hit.label
                        .split(" ")
                        .map((part) => part[0])
                        .slice(0, 2)
                        .join("")}
                    </span>
                    <span className="search-result__body">
                      <strong>{hit.label}</strong>
                      <small>
                        {hit.role ?? "Researcher"}
                        {hit.institution ? ` · ${hit.institution}` : ""}
                      </small>
                    </span>
                  </button>
                ))}
                {orgMatches.length > 0 && (
                  <p className="search-results__label">Organizations</p>
                )}
                {orgMatches.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    className="search-result"
                    onClick={() => focusResult(hit.id)}
                  >
                    <span className="search-result__avatar search-result__avatar--institution">
                      {hit.label.slice(0, 2).toUpperCase()}
                    </span>
                    <span className="search-result__body">
                      <strong>{hit.label}</strong>
                      <small>{hit.orgKind}</small>
                    </span>
                  </button>
                ))}
                {peopleMatches.length === 0 && orgMatches.length === 0 && (
                  <p className="search-results__empty">No matching records</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <nav className="site-header__nav" aria-label="Main">
        {showDirectorySearch && (
          <button
            type="button"
            className="site-header__guide"
            onClick={startTour}
            aria-label="Start guided tour"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.75" />
              <path d="M12 11V16M12 8V8.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <span className="site-header__guide-label">Guide</span>
          </button>
        )}
        <Link
          className={`site-header__link${isDataPage ? " is-active" : ""}`}
          to="/data"
        >
          Tables
        </Link>
        <span className="status-pill" data-tour="evidence">
          <i aria-hidden="true" />
          <span className="status-pill__label">Evidence-aware</span>
        </span>
        <span className="site-header__edition">Preview</span>
      </nav>
    </header>
  );
}

function GraphShellLayout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const isGraphHome = location.pathname === "/";

  return (
    <div
      className={`app-shell app-shell--graph${isGraphHome ? " app-shell--canvas" : ""}`}
    >
      <AppHeader />
      <main className="app-main">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "") || undefined}>
      <OnboardingProvider>
        <GraphShellLayout>
          <Routes>
            <Route path="/" element={<HomeGraphPage />} />
            <Route path="/person/:id" element={<PersonRoute />} />
            <Route path="/institution/:id" element={<InstitutionRoute />} />
            <Route path="/explore/:nodeId" element={<ExploreGraphPage />} />
            <Route path="/data" element={<DataTablesPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </GraphShellLayout>
      </OnboardingProvider>
    </BrowserRouter>
  );
}
