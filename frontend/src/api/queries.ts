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
      }
      publications {
        id
        title
        year
        citedByCount
        authorPosition
      }
      closestPeople {
        id
        label
        role
        institution
        relation
        detail
      }
    }
  }
`;

export const UNIVERSITIES = gql`
  query Universities($on: Date) {
    universities(on: $on) {
      id
      label
      orgKind
      sublabel
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

export interface CareerEntry {
  title: string | null;
  organization: string;
  affiliationKind: string;
  positionRank: string | null;
  isPrimary: boolean;
  startsAt: string | null;
  endsAt: string | null;
}

export interface PersonPublication {
  id: string;
  title: string;
  year: number;
  citedByCount: number | null;
  authorPosition: number;
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
}

export interface UniversitiesData {
  universities: OrgUnit[];
}

export interface OrgChildrenData {
  orgChildren: OrgUnit[];
}

export interface OrgChildrenVars {
  parentId: string;
  on?: string;
}

export const PAGE_SIZE = 24;
