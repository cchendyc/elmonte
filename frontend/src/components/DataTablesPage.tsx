import { useMemo, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { Link, useSearchParams } from "react-router-dom";
import { PROJECTION, type ProjectionData } from "../api/projection";
import { UNIVERSITIES, type UniversitiesData } from "../api/queries";

type TableTab = "people" | "organizations";

/**
 * Diagnostic table browser over the live database: a paginated, sortable
 * people table (from the atlas projection) and an organizations table (from
 * the org tree). Kept deliberately simple — it is a data-inspection surface,
 * not a management UI.
 */
export function DataTablesPage() {
  const [tab, setTab] = useState<TableTab>("people");
  const [page, setPage] = useState(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const PAGE_SIZE = 50;

  const { data: projection, loading: peopleLoading, error: peopleError } =
    useQuery<ProjectionData>(PROJECTION, {
      variables: { view: "topic" },
      fetchPolicy: "cache-first",
    });
  const { data: orgs, loading: orgsLoading, error: orgsError } =
    useQuery<UniversitiesData>(UNIVERSITIES, { fetchPolicy: "cache-first" });

  const people = projection?.projection.points ?? [];
  const universities = orgs?.universities ?? [];
  const query = (searchParams.get("q") ?? "").toLowerCase().trim();

  const peopleRows = useMemo(
    () =>
      people.filter((r) => {
        if (!query) return true;
        return `${r.label} ${r.institution ?? ""}`.toLowerCase().includes(query);
      }),
    [people, query],
  );
  const orgRows = useMemo(
    () =>
      universities.filter((r) => {
        if (!query) return true;
        return `${r.label} ${r.orgKind ?? ""}`.toLowerCase().includes(query);
      }),
    [universities, query],
  );

  const rows = tab === "people" ? peopleRows : orgRows;
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);

  return (
    <div className="data-tables">
      <header className="page-toolbar">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Atlas</Link>
          <span className="breadcrumb__sep" aria-hidden="true">/</span>
          <span className="breadcrumb__current">Tables</span>
        </nav>
        <label className="data-tables__search">
          <span className="sr-only">Filter rows</span>
          <input
            type="search"
            placeholder="Filter by name or institution…"
            value={query}
            onChange={(e) => {
              setSearchParams(e.target.value ? { q: e.target.value } : {});
              setPage(0);
            }}
          />
        </label>
      </header>

      <div className="data-tables__tabs" role="tablist" aria-label="Table type">
        {(["people", "organizations"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            className={`data-tables__tab${tab === t ? " is-active" : ""}`}
            onClick={() => {
              setTab(t);
              setPage(0);
            }}
          >
            {t === "people" ? "People" : "Organizations"}
          </button>
        ))}
      </div>

      {tab === "people" && peopleLoading && <p role="status">Loading people…</p>}
      {tab === "people" && peopleError && (
        <p className="detail-panel__empty">Could not load people.</p>
      )}
      {tab === "organizations" && orgsLoading && <p role="status">Loading organizations…</p>}
      {tab === "organizations" && orgsError && (
        <p className="detail-panel__empty">Could not load organizations.</p>
      )}

      {!peopleLoading && !peopleError && tab === "people" && (
        <table className="data-tables__table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Role</th>
              <th>Institution</th>
              <th>Field cluster</th>
            </tr>
          </thead>
          <tbody>
            {peopleRows
              .slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
              .map((r) => (
              <tr key={r.id}>
                <td>
                  <Link className="data-tables__link" to={`/?focus=${r.id}`}>
                    {r.label}
                  </Link>
                </td>
                <td>{r.rank ?? ""}</td>
                <td>{r.institution ?? ""}</td>
                <td>{r.clusterLabel ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!orgsLoading && !orgsError && tab === "organizations" && (
        <table className="data-tables__table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>Children</th>
              <th>Roster</th>
            </tr>
          </thead>
          <tbody>
            {orgRows
              .slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)
              .map((r) => (
              <tr key={r.id}>
                <td>
                  <Link className="data-tables__link" to={`/?focus=${r.id}`}>
                    {r.label}
                  </Link>
                </td>
                <td>{r.orgKind ?? ""}</td>
                <td>{r.sublabel ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {rows.length === 0 && !peopleLoading && !peopleError && (
        <p className="detail-panel__empty">No rows match the filter.</p>
      )}

      {pageCount > 1 && (
        <nav className="data-tables__pager" aria-label="Pagination">
          <button
            type="button"
            className="button-secondary"
            disabled={safePage === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </button>
          <span>
            {safePage + 1} / {pageCount} · {rows.length} rows
          </span>
          <button
            type="button"
            className="button-secondary"
            disabled={safePage >= pageCount - 1}
            onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          >
            Next
          </button>
        </nav>
      )}
    </div>
  );
}
