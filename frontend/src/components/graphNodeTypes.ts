import { CohortNode, OrgNode, PeerMore, PersonNode } from "./graphNodes";

/**
 * React Flow node-type registry. Kept in its own file because Fast Refresh
 * treats non-component exports from a component file as a hot-reload hazard.
 */
export const nodeTypes = {
  person: PersonNode,
  org: OrgNode,
  cohort: CohortNode,
  peer_more: PeerMore,
};
