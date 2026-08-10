import { gql } from "@apollo/client";

export const PROJECTION = gql`
  query Projection($view: String!) {
    projection(view: $view) {
      runId
      algorithm
      view
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
        clusterId
        clusterLabel
        retiredAt
        lastPublicationYear
      }
      clusters {
        id
        label
        fieldName
        memberCount
        cx
        cy
        colorSlot
      }
      edges {
        sourceCluster
        targetCluster
        collaborationWeight
        topicWeight
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
  clusterId: number | null;
  clusterLabel: string | null;
  retiredAt: string | null;
  lastPublicationYear: number | null;
}

export interface ProjectionCluster {
  id: number;
  label: string;
  fieldName: string | null;
  memberCount: number;
  cx: number;
  cy: number;
  colorSlot: number;
}

export interface ProjectionClusterEdge {
  sourceCluster: number;
  targetCluster: number;
  collaborationWeight: number | null;
  topicWeight: number | null;
}

export interface Projection {
  runId: string;
  algorithm: string;
  view: string;
  pointCount: number;
  points: ProjectionPoint[];
  clusters: ProjectionCluster[];
  edges: ProjectionClusterEdge[];
}

export interface ProjectionData {
  projection: Projection;
}

export interface ProjectionVars {
  view: string;
}
