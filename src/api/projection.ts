/**
 * Typed shape and GraphQL document for the person scatter projection.
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
        similarityGroup
        retiredAt
        lastPublicationYear
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
  similarityGroup: number | null;
  retiredAt: string | null;
  lastPublicationYear: number | null;
}

export interface Projection {
  runId: string;
  algorithm: string;
  pointCount: number;
  points: ProjectionPoint[];
}

export interface ProjectionData {
  projection: Projection;
}
