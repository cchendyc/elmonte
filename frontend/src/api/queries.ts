/**
 * GraphQL documents used by the frontend. Kept flat and hand-typed —
 * response shapes come from `graphSession.ts` (`HopResponse`, `PeoplePage`)
 * and a small local `SearchResults` type.
 *
 * We deliberately select every scalar on every type: the session reducer
 * expects them all to be present, and Apollo's cache normalises equal
 * documents so there's no cost to over-selecting for a single write path.
 */

import { gql } from "@apollo/client";
import type { HopResponse, PeoplePage } from "../lib/graphSession";

export const SEARCH = gql`
  query Search($q: String!, $limit: Int) {
    search(q: $q, limit: $limit) {
      people {
        id
        label
        role
        institution
        orcid
        researchArea
        publicationCount
        lastPublicationYear
      }
      orgs {
        id
        label
        orgKind
      }
    }
  }
`;

export const EXPAND = gql`
  query Expand($id: ID!, $on: Date) {
    expand(id: $id, on: $on) {
      focusId
      nodes {
        id
        kind
        label
        sublabel
        institution
        orgKind
        rank
        stub
        retiredAt
      }
      links {
        source
        target
        relation
        weight
        label
      }
      groups {
        key
        label
        count
      }
      page {
        ownerId
        groupKey
        offset
        total
        items {
          id
          label
          sublabel
          rank
        }
      }
    }
  }
`;

export const PERSON = gql`
  query Person($id: ID!, $on: Date) {
    person(id: $id, on: $on) {
      id
      label
      role
      institution
      biography
      homepageUrl
      cvUrl
      orcid
      careerTimeline {
        title
        organization
        affiliationKind
        positionRank
        isPrimary
        startsAt
        endsAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      publications {
        id
        title
        year
        citedByCount
        authorPosition
        doi
        venue
      }
      closestPeople {
        id
        label
        role
        institution
        relation
        detail
      }
      awards {
        name
        awardedAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      grants {
        title
        funder
        role
        awardNumber
        amount
        currency
        startsAt
        endsAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      personTopics {
        displayName
        score
        worksCount
      }
      personConcepts {
        displayName
        score
        rank
      }
    }
  }
`;

export const PERSON_EXPORT = gql`
  query PersonExport($personId: ID!) {
    personExport(personId: $personId) {
      id
      label
      firstname
      middlename
      lastname
      biography
      homepageUrl
      cvUrl
      role
      institution
      aliases
      externalIdentifiers {
        provider
        externalId
      }
      careerTimeline {
        title
        organization
        affiliationKind
        positionRank
        isPrimary
        startsAt
        endsAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      publications {
        id
        title
        year
        citedByCount
        authorPosition
        doi
        venue
      }
      closestPeople {
        id
        label
        role
        institution
        relation
        detail
      }
      personTopics {
        displayName
        score
        worksCount
      }
      personConcepts {
        displayName
        score
        rank
      }
      awards {
        name
        awardedAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      grants {
        title
        funder
        role
        awardNumber
        amount
        currency
        startsAt
        endsAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
      personRelationships {
        type
        otherPersonId
        otherPersonLabel
        startsAt
        endsAt
        verificationStatus
        sources {
          label
          url
          sourceKind
        }
      }
    }
  }
`;

export const UNIVERSITIES = gql`
  query Universities($on: Date, $limit: Int) {
    universities(on: $on, limit: $limit) {
      id
      label
      orgKind
      sublabel
      childCount
      rosterCount
    }
  }
`;

export const ORG_CHILDREN = gql`
  query OrgChildren($parentId: ID!, $on: Date) {
    orgChildren(parentId: $parentId, on: $on) {
      id
      label
      orgKind
      sublabel
      childCount
      rosterCount
    }
  }
`;

export const ORG_PROFILE = gql`
  query OrgProfile($id: ID!, $on: Date) {
    org(id: $id, on: $on) {
      id
      label
      name
      orgKind
      sublabel
      country
      homepageUrl
      description
      parent {
        id
        label
        orgKind
        sublabel
        childCount
        rosterCount
      }
      children {
        id
        label
        orgKind
        sublabel
        childCount
        rosterCount
      }
      rosterCount
      subtreePeopleCount
      externalIdentifiers {
        provider
        externalId
      }
    }
  }
`;

export const FETCH_PAGE = gql`
  query FetchPage(
    $ownerId: ID!
    $groupKey: String
    $offset: Int
    $limit: Int
    $on: Date
  ) {
    pages(
      ownerId: $ownerId
      groupKey: $groupKey
      offset: $offset
      limit: $limit
      on: $on
    ) {
      ownerId
      groupKey
      offset
      total
      items {
        id
        label
        sublabel
        rank
      }
    }
  }
`;

// ---------------------------------------------------------------------------
// Typed response shapes
// ---------------------------------------------------------------------------

export interface SearchPersonHit {
  id: string;
  label: string;
  role: string | null;
  institution: string | null;
  orcid: string | null;
  researchArea: string | null;
  publicationCount: number | null;
  lastPublicationYear: number | null;
}

export interface SearchOrgHit {
  id: string;
  label: string;
  orgKind: string | null;
}

export interface SearchResults {
  people: SearchPersonHit[];
  orgs: SearchOrgHit[];
}

export interface SearchData {
  search: SearchResults;
}
export interface SearchVars {
  q: string;
  limit?: number;
}

export interface ExpandData {
  expand: HopResponse;
}
export interface ExpandVars {
  id: string;
  on?: string;
}

export interface FetchPageData {
  pages: PeoplePage;
}
export interface FetchPageVars {
  ownerId: string;
  groupKey?: string;
  offset?: number;
  limit?: number;
  on?: string;
}

export type VerificationStatus = "verified" | "unverified" | "disputed";

export interface EvidenceSource {
  label: string | null;
  url: string;
  sourceKind: string;
}

export interface CareerEntry {
  title: string | null;
  organization: string;
  affiliationKind: string;
  positionRank: string | null;
  isPrimary: boolean;
  startsAt: string | null;
  endsAt: string | null;
  verificationStatus: VerificationStatus;
  sources: EvidenceSource[];
}

export interface PersonPublication {
  id: string;
  title: string;
  year: number;
  citedByCount: number | null;
  authorPosition: number;
  doi: string | null;
  venue: string | null;
}

export interface PersonAward {
  name: string;
  awardedAt: string | null;
  verificationStatus: VerificationStatus;
  sources: EvidenceSource[];
}

export interface PersonGrant {
  title: string;
  funder: string;
  role: string;
  awardNumber: string | null;
  amount: number | null;
  currency: string | null;
  startsAt: string | null;
  endsAt: string | null;
  verificationStatus: VerificationStatus;
  sources: EvidenceSource[];
}

export interface PersonTopic {
  displayName: string;
  score: number;
  worksCount: number;
}

export interface PersonConcept {
  displayName: string;
  score: number | null;
  rank: number | null;
}

export interface ClosestPerson {
  id: string;
  label: string;
  role: string | null;
  institution: string | null;
  relation: string;
  detail: string | null;
}

export interface PersonProfile {
  id: string;
  label: string;
  role: string | null;
  institution: string | null;
  biography: string | null;
  homepageUrl: string | null;
  cvUrl: string | null;
  orcid: string | null;
  careerTimeline: CareerEntry[];
  publications: PersonPublication[];
  closestPeople: ClosestPerson[];
  awards: PersonAward[];
  grants: PersonGrant[];
  personTopics: PersonTopic[];
  personConcepts: PersonConcept[];
}

export interface PersonData {
  person: PersonProfile | null;
}

export interface PersonVars {
  id: string;
  on?: string;
}

export interface OrgUnit {
  id: string;
  label: string;
  orgKind: string;
  sublabel: string | null;
  childCount?: number | null;
  rosterCount?: number | null;
}

export interface OrgIdentifier {
  provider: string;
  externalId: string;
}

export interface OrgProfile {
  id: string;
  label: string;
  name: string;
  orgKind: string;
  sublabel: string | null;
  country: string | null;
  homepageUrl: string | null;
  description: string | null;
  parent: OrgUnit | null;
  children: OrgUnit[];
  rosterCount: number;
  subtreePeopleCount: number;
  externalIdentifiers: OrgIdentifier[];
}

export interface OrgProfileData {
  org: OrgProfile | null;
}

export interface OrgProfileVars {
  id: string;
  on?: string;
}

export interface UniversitiesData {
  universities: OrgUnit[];
}

export interface UniversitiesVars {
  on?: string;
  limit?: number;
}

export interface OrgChildrenData {
  orgChildren: OrgUnit[];
}

export interface OrgChildrenVars {
  parentId: string;
  on?: string;
}

export const PAGE_SIZE = 24;
