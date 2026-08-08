import { useState } from "react";
import { useQuery } from "@apollo/client/react";
import {
  PERSON,
  type PersonData,
  type PersonVars,
  type CareerEntry,
} from "../api/queries";

type Tab = "timeline" | "publications" | "people";

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

export function PersonDetailPanel({ personId, onFocusPerson, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("timeline");
  const { data, loading, error } = useQuery<PersonData, PersonVars>(PERSON, {
    variables: { id: personId },
    fetchPolicy: "cache-and-network",
  });

  const profile = data?.person;

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
            <p className="detail-panel__lede">{profile.biography}</p>
          )}

          {profile.homepageUrl && (
            <a
              className="button-secondary detail-panel__link"
              href={profile.homepageUrl}
              target="_blank"
              rel="noreferrer"
            >
              Homepage →
            </a>
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
                      <li key={`${entry.organization}-${entry.startsAt ?? index}`}>
                        <div className="timeline__item--static">
                          <div className="timeline__range">{formatRange(entry)}</div>
                          <div className="timeline__title">
                            {entry.title ?? formatKind(entry.affiliationKind)}
                          </div>
                          <div className="timeline__subtitle">{entry.organization}</div>
                          {entry.isPrimary && (
                            <span className="timeline__badge">Primary</span>
                          )}
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
                    {profile.publications.map((pub) => (
                      <li key={pub.id} className="person-profile-pane__pub">
                        <strong>{pub.title}</strong>
                        <small>
                          {pub.year}
                          {pub.citedByCount != null
                            ? ` · ${pub.citedByCount} citations`
                            : ""}
                          {` · author #${pub.authorPosition}`}
                        </small>
                      </li>
                    ))}
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
