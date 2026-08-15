import { Link, Navigate } from "react-router-dom";
import { useQuery } from "@apollo/client/react";
import {
  ORG_PROFILE,
  type OrgProfileData,
  type OrgProfileVars,
  type OrgUnit,
} from "../api/queries";
import { ExpandableText } from "./ExpandableText";
import { OrgChart } from "./OrgChart";
import { safeHttpUrl } from "../lib/safeUrl";

interface Props {
  institutionId: string;
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function identifierHref(provider: string, externalId: string): string | null {
  switch (provider) {
    case "ror":
      return safeHttpUrl(`https://ror.org/${externalId}`);
    case "wikidata":
      return safeHttpUrl(`https://www.wikidata.org/wiki/${externalId}`);
    case "official_url":
      return safeHttpUrl(externalId);
    default:
      return null;
  }
}

function OrgUnitLink({ unit }: { unit: OrgUnit }) {
  return (
    <Link
      className="institution-profile__child"
      to={`/institution/${unit.id}`}
    >
      <strong>{unit.label}</strong>
      <span>
        {titleCase(unit.orgKind)}
        {unit.childCount ? ` · ${unit.childCount} units` : ""}
        {unit.rosterCount ? ` · ${unit.rosterCount} people` : ""}
      </span>
    </Link>
  );
}

/**
 * Institution-level route: directory metadata, hierarchy summary, and the
 * focusable org chart.  The profile data comes from the same GraphQL layer as
 * the rest of the app, so historical as-of dates and evidence status stay on
 * one backend path.
 */
export function InstitutionPage({ institutionId }: Props) {
  const isValidId = institutionId.startsWith("o:");
  const { data, loading, error } = useQuery<OrgProfileData, OrgProfileVars>(
    ORG_PROFILE,
    {
      variables: { id: institutionId },
      fetchPolicy: "cache-and-network",
      skip: !isValidId,
    },
  );
  const profile = data?.org;
  const homepage = safeHttpUrl(profile?.homepageUrl);

  if (!isValidId) {
    return <Navigate to="/" replace />;
  }

  if (!loading && !error && data && !profile) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="institution-page">
      <header className="page-toolbar">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Atlas</Link>
          <span className="breadcrumb__sep" aria-hidden="true">/</span>
          <span className="breadcrumb__current">
            {profile?.label ?? "Organization"}
          </span>
        </nav>
        <Link className="button-secondary" to={`/?focus=${institutionId}`}>
          Open in explorer
        </Link>
      </header>

      <section className="institution-profile" data-testid="institution-profile">
        {loading && !profile && (
          <p className="detail-panel__empty">Loading organization profile…</p>
        )}
        {error && (
          <p className="detail-panel__empty">
            Could not load organization profile. The chart below may still work.
          </p>
        )}
        {profile && (
          <>
            <div className="institution-profile__identity">
              <div className="institution-profile__title">
                <span className="institution-profile__kicker">
                  {titleCase(profile.orgKind)}
                </span>
                <h1>{profile.label}</h1>
                {profile.name !== profile.label && (
                  <p className="institution-profile__formal-name">
                    {profile.name}
                  </p>
                )}
              </div>
              <div className="institution-profile__meta">
                {profile.country && (
                  <span className="institution-profile__stat">
                    <small>Country</small>
                    <strong>{profile.country}</strong>
                  </span>
                )}
                <span className="institution-profile__stat">
                  <small>Direct roster</small>
                  <strong>{profile.rosterCount}</strong>
                </span>
                <span className="institution-profile__stat">
                  <small>People in subtree</small>
                  <strong>{profile.subtreePeopleCount}</strong>
                </span>
                <span className="institution-profile__stat">
                  <small>Child units</small>
                  <strong>{profile.children.length}</strong>
                </span>
              </div>
            </div>

            {profile.description && (
              <ExpandableText
                text={profile.description}
                className="institution-profile__description"
              />
            )}

            {(homepage || profile.externalIdentifiers.length > 0) && (
              <div className="institution-profile__links">
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
                {profile.externalIdentifiers.map((identifier) => {
                  const href = identifierHref(
                    identifier.provider,
                    identifier.externalId,
                  );
                  return href ? (
                    <a
                      key={`${identifier.provider}:${identifier.externalId}`}
                      className="button-secondary detail-panel__link"
                      href={href}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {identifier.provider} →
                    </a>
                  ) : (
                    <span
                      key={`${identifier.provider}:${identifier.externalId}`}
                      className="institution-profile__identifier"
                    >
                      {identifier.provider}: {identifier.externalId}
                    </span>
                  );
                })}
              </div>
            )}

            {(profile.parent || profile.children.length > 0) && (
              <div className="institution-profile__hierarchy">
                {profile.parent && (
                  <div className="institution-profile__parent">
                    <small>Part of</small>
                    <OrgUnitLink unit={profile.parent} />
                  </div>
                )}
                {profile.children.length > 0 && (
                  <div className="institution-profile__children">
                    <small>Units</small>
                    <div className="institution-profile__child-list">
                      {profile.children.map((child) => (
                        <OrgUnitLink key={child.id} unit={child} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </section>

      <div className="institution-page__canvas">
        <OrgChart focusId={institutionId} minHeight="calc(100vh - 320px)" />
      </div>
    </div>
  );
}
