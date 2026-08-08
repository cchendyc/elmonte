/**
 * Typed shape and GraphQL document for the person scatter projection.
 *
 * The projection is served in one shot — at Berkeley scale (~400 points)
 * this is cheap and lets the frontend colour, filter, and pan/zoom without
 * further round trips. If we ever grow to tens of thousands of points, add
 * a viewport filter on the server and paginate.
 */

import { gql } from "@apollo/client";

export const PROJECTION = gql`
  query Projection {
    projection {
      runId
      algorithm
      pointCount
      points {
        id
        label
        x
        y
        institution
        institutionId
        rank
        impact
      }
      edges {
        sourceId
        targetId
        weight
      }
    }
  }
`;

export interface ProjectionPoint {
  id: string;
  label: string;
  x: number;
  y: number;
  institution: string | null;
  institutionId: string | null;
  rank: string | null;
  impact: number;
}

export interface ProjectionEdge {
  sourceId: string;
  targetId: string;
  weight: number;
}

export interface Projection {
  runId: string;
  algorithm: string;
  pointCount: number;
  points: ProjectionPoint[];
  edges: ProjectionEdge[];
}

export interface ProjectionData {
  projection: Projection;
}
