import { useRef, useState } from "react";
import { useLazyQuery, useQuery } from "@apollo/client/react";
import {
  PERSON,
  PERSON_EXPORT,
  type CareerEntry,
  type EvidenceSource,
  type PersonData,
  type PersonVars,
} from "../api/queries";
import { ExpandableText } from "./ExpandableText";
import { apiUrl } from "../lib/apiUrl";
import { doiUrl, isOrcid, safeHttpUrl } from "../lib/safeUrl";

type Tab = "timeline" | "publications" | "topics" | "funding" | "people";

interface Props {
  personId: string;
  onFocusPerson: (id: string) => void;
  onClose: () => void;
}

function initials(label: string): string {
  return label
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("");
}

function formatRange(entry: CareerEntry): string {
  const start = entry.startsAt ? entry.startsAt.slice(0, 4) : null;
  const end = entry.endsAt ? entry.endsAt.slice(0, 4) : "present";
  if (start && end) return `${start} – ${end}`;
  if (start) return `${start} – present`;
  if (entry.endsAt) return `until ${entry.endsAt.slice(0, 4)}`;
  return "dates unknown";
}

function formatKind(kind: string): string {
  return kind.replaceAll("_", " ");
}

function verificationLabel(status: string): string {
  switch (status) {
    case "verified":
      return "Verified";
    case "disputed":
      return "Disputed";
    case "unverified":
      return "Unverified";
    default:
      return status;
  }
}

function relationLabel(relation: string): string {
  switch (relation) {
    case "advisor":
      return "Advisor";
    case "advisee":
      return "Advisee";
    case "coauthor":
      return "Coauthor";
    case "colleague":
      return "Colleague";
    default:
      return relation;
  }
}

function formatMoney(amount: number | null, currency: string | null): string {
  if (amount == null) return "";
  const code = (currency ?? "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    // A pipeline-provided 3-letter code may not be an ISO 4217 currency
    // (Intl throws a RangeError). Fall back to a locale-neutral rendering
    // instead of crashing the profile panel.
    return `${code} ${amount.toLocaleString(undefined, {
      maximumFractionDigits: 2,
    })}`;
  }
}

/** Evidence is the product's core promise: render each source as a validated
 *  link with its source kind. Unsafe URLs are shown as text, never as href. */
function EvidenceLinks({ sources }: { sources: EvidenceSource[] }) {
  if (sources.length === 0) return null;
  return (
    <ul className="detail-panel__evidence">
      {sources.map((source, index) => {
        const href = safeHttpUrl(source.url);
        const label = source.label || source.sourceKind;
        return (
          <li key={`${source.url}-${index}`}>
            {href ? (
              <a href={href} target="_blank" rel="noreferrer">
                {label} ↗
              </a>
            ) : (
              <span>{label}</span>
            )}
            <small>{source.sourceKind}</small>
          </li>
        );
      })}
    </ul>
  );
}

function scoreWidth(score: number | null | undefined): string {
  if (score == null) return "0%";
  return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;
}

interface PersonExportResponse {
  personExport: Record<string, unknown>;
}

export function PersonDetailPanel({ personId, onFocusPerson, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("timeline");
  const [exportError, setExportError] = useState<string | null>(null);
  const { data, loading, error } = useQuery<PersonData, PersonVars>(PERSON, {
    variables: { id: personId },
    fetchPolicy: "cache-and-network",
  });
  const [exportPerson, { loading: exporting }] = useLazyQuery<
    PersonExportResponse,
    { personId: string }
  >(PERSON_EXPORT, { fetchPolicy: "network-only" });

  // Stale-data guard: during a person switch Apollo briefly reports no data —
  // fall back to the last profile for the SAME person id instead of flashing
  // "No profile found." (or, worse, the previous person's profile).
  const profileRef = useRef<{ personId: string; profile: PersonData["person"] } | null>(null);
  if (data?.person) {
    profileRef.current = { personId, profile: data.person };
  }
  const profile =
    data?.person ??
    (profileRef.current?.personId === personId ? profileRef.current.profile : undefined);

  // Only render links when the value passes validation — homepage and ORCID
  // come from the data pipeline, so treat them as untrusted input.
  const homepage = profile ? safeHttpUrl(profile.homepageUrl) : null;
  const cv = profile?.cvUrl ? apiUrl(profile.cvUrl) : null;
  const orcid = profile ? (isOrcid(profile.orcid) ? profile.orcid : null) : null;

  async function downloadDataExport() {
    setExportError(null);
    try {
      const result = await exportPerson({ variables: { personId } });
      const payload = result.data?.personExport;
      if (!payload) {
        setExportError("No export data was returned for this person.");
        return;
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `elmonte-person-${personId.slice(2)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Firefox and Safari can cancel a download when the object URL is
      // revoked synchronously in the same task as the click.
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      setExportError("Could not download the data export. Please try again.");
    }
  }

  return (
    <aside className="person-profile-pane detail-panel" aria-label="Person profile">
      <div className="detail-panel__kicker">
        <span>Profile</span>
        <button
          type="button"
          className="person-profile-pane__close"
          onClick={onClose}
          aria-label="Close profile"
        >
          ×
        </button>
      </div>

      {loading && !profile && (
        <p className="detail-panel__empty">Loading profile…</p>
      )}

      {error && (
        <p className="detail-panel__empty">Could not load profile.</p>
      )}

      {!loading && !error && !profile && (
        <p className="detail-panel__empty">No profile found.</p>
      )}

      {profile && (
        <>
          <div className="profile-identity">
            <span className="record-heading__avatar search-result__avatar search-result__avatar--person">
              {initials(profile.label)}
            </span>
            <div>
              <h2 className="detail-panel__title">{profile.label}</h2>
              {profile.role && (
                <p className="detail-panel__meta">{profile.role}</p>
              )}
              {profile.institution && (
                <p className="detail-panel__meta">{profile.institution}</p>
              )}
            </div>
          </div>

          {profile.biography && (
            <ExpandableText
              text={profile.biography}
              className="detail-panel__lede"
            />
          )}

          {profile.awards.length > 0 && (
            <div className="detail-panel__awards">
              {profile.awards.map((award) => (
                <span
                  key={`${award.name}-${award.awardedAt ?? ""}`}
                  className="detail-panel__award"
                  title={`${verificationLabel(award.verificationStatus)}${
                    award.sources.length > 0
                      ? ` · ${award.sources.length} source${
                          award.sources.length === 1 ? "" : "s"
                        }`
                      : ""
                  }`}
                >
                  {award.name}
                  {award.awardedAt ? ` · ${award.awardedAt.slice(0, 4)}` : ""}
                </span>
              ))}
            </div>
          )}

          <div className="detail-panel__links">
            {homepage && (
              <a
                className="button-secondary detail-panel__link"
                href={homepage}
                target="_blank"
                rel="noreferrer"
              >
                Homepage →
              </a>
            )}
            {cv && (
              <a
                className="button-secondary detail-panel__link"
                href={cv}
                target="_blank"
                rel="noreferrer"
              >
                CV →
              </a>
            )}
            {orcid && (
              <a
                className="button-secondary detail-panel__link"
                href={`https://orcid.org/${orcid}`}
                target="_blank"
                rel="noreferrer"
              >
                ORCID iD: {orcid}
              </a>
            )}
            <button
              type="button"
              className="button-secondary detail-panel__link"
              onClick={downloadDataExport}
              disabled={exporting}
            >
              {exporting ? "Preparing export…" : "Download data (JSON)"}
            </button>
          </div>
          {exportError && (
            <p className="detail-panel__empty" role="alert">
              {exportError}
            </p>
          )}

          <div className="detail-panel__tabs" role="tablist" aria-label="Profile sections">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "timeline"}
              className={tab === "timeline" ? "is-active" : undefined}
              onClick={() => setTab("timeline")}
            >
              Timeline ({profile.careerTimeline.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "publications"}
              className={tab === "publications" ? "is-active" : undefined}
              onClick={() => setTab("publications")}
            >
              Papers ({profile.publications.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "topics"}
              className={tab === "topics" ? "is-active" : undefined}
              onClick={() => setTab("topics")}
            >
              Topics ({profile.personTopics.length + profile.personConcepts.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "funding"}
              className={tab === "funding" ? "is-active" : undefined}
              onClick={() => setTab("funding")}
            >
              Funding ({profile.grants.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "people"}
              className={tab === "people" ? "is-active" : undefined}
              onClick={() => setTab("people")}
            >
              People ({profile.closestPeople.length})
            </button>
          </div>

          <div className="detail-panel__tab-content">
            {tab === "timeline" && (
              <>
                {profile.careerTimeline.length === 0 ? (
                  <p className="detail-panel__empty">
                    No career history recorded yet. Affiliations are added as
                    faculty directories and CVs are ingested.
                  </p>
                ) : (
                  <ol className="timeline person-profile-pane__timeline">
                    {profile.careerTimeline.map((entry, index) => (
                      <li key={`${entry.organization}-${entry.startsAt ?? ""}-${index}`}>
                        <div className="timeline__item--static">
                          <div className="timeline__range">{formatRange(entry)}</div>
                          <div className="timeline__title">
                            {entry.title ?? formatKind(entry.affiliationKind)}
                          </div>
                          <div className="timeline__subtitle">{entry.organization}</div>
                          <div className="timeline__badges">
                            {entry.isPrimary && (
                              <span className="timeline__badge">Primary</span>
                            )}
                            <span
                              className={`timeline__badge timeline__badge--${entry.verificationStatus}`}
                              title={
                                entry.sources.length > 0
                                  ? `${entry.sources.length} source${
                                      entry.sources.length === 1 ? "" : "s"
                                    }`
                                  : "No linked source"
                              }
                            >
                              {verificationLabel(entry.verificationStatus)}
                            </span>
                          </div>
                          <EvidenceLinks sources={entry.sources} />
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </>
            )}

            {tab === "publications" && (
              <>
                {profile.publications.length === 0 ? (
                  <p className="detail-panel__empty">
                    No publications linked yet. Bibliography data is still being
                    connected for many researchers in the preview corpus.
                  </p>
                ) : (
                  <ul className="person-profile-pane__list">
                    {profile.publications.map((pub) => {
                      const doi = doiUrl(pub.doi);
                      return (
                        <li key={pub.id} className="person-profile-pane__pub">
                          {doi ? (
                            <a href={doi} target="_blank" rel="noreferrer">
                              <strong>{pub.title}</strong>
                            </a>
                          ) : (
                            <strong>{pub.title}</strong>
                          )}
                          <small>
                            {pub.year}
                            {pub.venue ? ` · ${pub.venue}` : ""}
                            {pub.citedByCount != null
                              ? ` · ${pub.citedByCount} citations`
                              : ""}
                            {` · author #${pub.authorPosition}`}
                          </small>
                        </li>
                      );
                    })}
                  </ul>
                )}
                <p className="detail-panel__attribution">
                  Publication data from{" "}
                  <a
                    href="https://openalex.org"
                    target="_blank"
                    rel="noreferrer"
                  >
                    OpenAlex
                  </a>
                </p>
              </>
            )}

            {tab === "topics" && (
              <div className="person-profile-pane__topics">
                <section aria-label="Research topics">
                  <h3>Research topics</h3>
                  {profile.personTopics.length === 0 ? (
                    <p className="detail-panel__empty">
                      No OpenAlex topic profile has been computed yet.
                    </p>
                  ) : (
                    <ul className="topic-score-list">
                      {profile.personTopics.map((topic) => (
                        <li key={topic.displayName}>
                          <div>
                            <span>{topic.displayName}</span>
                            <small>
                              {topic.worksCount} work
                              {topic.worksCount === 1 ? "" : "s"}
                            </small>
                          </div>
                          <span className="topic-score-list__bar">
                            <span style={{ width: scoreWidth(topic.score) }} />
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
                <section aria-label="Research fields">
                  <h3>Research fields</h3>
                  {profile.personConcepts.length === 0 ? (
                    <p className="detail-panel__empty">
                      No research fields have been assigned yet.
                    </p>
                  ) : (
                    <ul className="concept-rank-list">
                      {profile.personConcepts.map((concept) => (
                        <li key={concept.displayName}>
                          <span>{concept.displayName}</span>
                          {concept.rank != null && <small>#{concept.rank}</small>}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            )}

            {tab === "funding" && (
              <>
                {profile.grants.length === 0 ? (
                  <p className="detail-panel__empty">
                    No grant funding has been linked to this profile yet.
                  </p>
                ) : (
                  <ul className="person-profile-pane__list">
                    {profile.grants.map((grant) => {
                      const amount = formatMoney(grant.amount, grant.currency);
                      return (
                        <li
                          key={`${grant.title}-${grant.awardNumber ?? ""}-${grant.startsAt ?? ""}`}
                          className="person-profile-pane__grant"
                        >
                          <strong>{grant.title}</strong>
                          <small>
                            {grant.funder}
                            {grant.role ? ` · ${formatKind(grant.role)}` : ""}
                            {grant.awardNumber ? ` · ${grant.awardNumber}` : ""}
                          </small>
                          {amount && <div className="grant-amount">{amount}</div>}
                          <div className="timeline__badges">
                            <span
                              className={`timeline__badge timeline__badge--${grant.verificationStatus}`}
                            >
                              {verificationLabel(grant.verificationStatus)}
                            </span>
                          </div>
                          <EvidenceLinks sources={grant.sources} />
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}

            {tab === "people" && (
              <>
                {profile.closestPeople.length === 0 ? (
                  <p className="detail-panel__empty">
                    No advisors, coauthors, or colleagues in the database yet.
                    Expand this person on the org chart to explore nearby nodes.
                  </p>
                ) : (
                  <div className="person-profile-pane__people">
                    {profile.closestPeople.map((person) => (
                      <button
                        key={person.id}
                        type="button"
                        className="search-result person-profile-pane__person"
                        onClick={() => onFocusPerson(person.id)}
                      >
                        <span className="search-result__avatar search-result__avatar--person">
                          {initials(person.label)}
                        </span>
                        <span className="search-result__body">
                          <strong>{person.label}</strong>
                          <small>
                            {relationLabel(person.relation)}
                            {person.detail ? ` · ${person.detail}` : ""}
                            {person.role ? ` · ${person.role}` : ""}
                            {person.institution ? ` · ${person.institution}` : ""}
                          </small>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
