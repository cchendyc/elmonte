import type { PositionRank } from "./positionRank";

// -----------------------------------------------------------------------------
// Exploration session
// -----------------------------------------------------------------------------
//
// The canvas is an ACCUMULATING graph, not a re-rendered tree. Every click
// fetches one level around a node and merges it in. Nothing the user has
// already revealed is thrown away — the graph is the trace of their
// investigation.
//
// INVARIANT: at most ONE edge between any two nodes. Links are stored in a map
// keyed by the unordered pair. The canvas only renders org hierarchy edges
// (placement and org_parent); advisor and coauthor relations stay in the
// profile panel, not on the graph.
// -----------------------------------------------------------------------------

export type NodeKind = "person" | "org";

export type Relation = "report" | "placement" | "org_parent" | "coauthor";

const SESSION_RELATIONS = new Set<Relation>(["placement", "org_parent", "report"]);

export interface HopNode {
  id: string;
  kind: NodeKind;
  label: string;
  sublabel?: string;
  /** Institution the node "belongs to" — used for the ghost stub above a
   *  cross-institution coauthor. */
  institution?: string;
  orgKind?: string;
  rank?: PositionRank;
  /** Set when this node arrived as a stub (a person imported only because
   *  they were another node's coauthor). Stubs get a dotted upstream edge to
   *  their institution and are not expanded further until clicked. */
  stub?: boolean;
  /** When the chart-anchor affiliation ended; null while still active. */
  retiredAt?: string | null;
}

export interface HopLink {
  source: string;
  target: string;
  relation: Relation;
  weight?: number;
  label?: string;
}

export interface GroupSummary {
  key: string;
  label: string;
  count: number;
}

export interface PagePerson {
  id: string;
  label: string;
  sublabel?: string;
  rank?: PositionRank;
}

export interface PeoplePage {
  ownerId: string;
  groupKey: string;
  offset: number;
  total: number;
  items: PagePerson[];
}

export interface HopResponse {
  focusId: string;
  nodes: HopNode[];
  links: HopLink[];
  /** Group summaries for the focus, if it's an org (or a person whose anchor
   *  org has a large roster). */
  groups?: GroupSummary[];
  /** First page of the focused group when there's only one obvious choice. */
  page?: PeoplePage;
}

export interface SessionNode extends HopNode {
  /** Sequence in which the user first revealed this node. Drives the trail. */
  step: number;
  /** True once we've fetched this node's 1-hop neighbourhood. */
  expanded: boolean;
  /** How the node entered the graph — used for the small provenance badge. */
  via?: Relation;
}

export interface SessionLink {
  key: string;
  source: string;
  target: string;
  relation: Relation;
  /** Every relation observed for this pair — surfaces on the label. */
  relations: Relation[];
  weight?: number;
  label?: string;
}

export interface GroupState {
  ownerId: string;
  groups: GroupSummary[];
  openKey?: string;
  /** Local rank filter for the open cohort. Reset when the cohort collapses. */
  rankFilter?: PositionRank;
}

export interface SessionState {
  nodes: Record<string, SessionNode>;
  links: Record<string, SessionLink>;
  trail: string[];
  focusId: string;
  loading: Record<string, boolean>;
  groups: Record<string, GroupState>;
  pages: Record<string, PeoplePage>;
  step: number;
}

// -----------------------------------------------------------------------------
// Link canonicalisation
// -----------------------------------------------------------------------------

export const linkKey = (a: string, b: string) => (a < b ? `${a}::${b}` : `${b}::${a}`);
export const pageKey = (ownerId: string, groupKey: string) => `${ownerId}::${groupKey}`;

/**
 * The API returns forward-only slices.  The session must ACCUMULATE them:
 * replacing `pages[key]` with the latest slice would silently drop the first
 * 24 names from an open roster band on every "Load more".
 */
export function mergePage(existing: PeoplePage | undefined, incoming: PeoplePage): PeoplePage {
  if (!existing) return incoming;
  const seen = new Set(existing.items.map((item) => item.id));
  const items = [...existing.items];
  for (const item of incoming.items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    items.push(item);
  }
  return {
    ownerId: incoming.ownerId,
    groupKey: incoming.groupKey,
    offset: Math.max(existing.offset, incoming.offset),
    total: incoming.total,
    items,
  };
}

const PRECEDENCE: Record<Relation, number> = {
  report: 4,
  placement: 3,
  org_parent: 2,
  coauthor: 1,
};

function mergeLink(existing: SessionLink | undefined, incoming: HopLink): SessionLink {
  const key = linkKey(incoming.source, incoming.target);
  if (!existing) {
    return {
      key,
      source: incoming.source,
      target: incoming.target,
      relation: incoming.relation,
      relations: [incoming.relation],
      weight: incoming.weight,
      label: incoming.label,
    };
  }

  const relations = existing.relations.includes(incoming.relation)
    ? existing.relations
    : [...existing.relations, incoming.relation];

  const takeIncoming = PRECEDENCE[incoming.relation] > PRECEDENCE[existing.relation];

  // Compose a label covering every relationship on this single edge.
  const parts: string[] = [];
  if (relations.includes("report")) parts.push("supervised");
  const weight = incoming.weight ?? existing.weight;
  if (relations.includes("coauthor") && weight) {
    parts.push(`${weight} paper${weight === 1 ? "" : "s"}`);
  }

  return {
    key,
    source: takeIncoming ? incoming.source : existing.source,
    target: takeIncoming ? incoming.target : existing.target,
    relation: takeIncoming ? incoming.relation : existing.relation,
    relations,
    weight,
    label: parts.length > 0 ? parts.join(" · ") : (existing.label ?? incoming.label),
  };
}

// -----------------------------------------------------------------------------
// Reducer
// -----------------------------------------------------------------------------

export type SessionAction =
  | { type: "loading"; id: string; value: boolean }
  | {
      type: "merge";
      focusId: string;
      nodes: HopNode[];
      links: HopLink[];
      groups?: GroupSummary[];
      page?: PeoplePage;
      /** Move the focus to `focusId` and append to the trail. */
      advanceTrail: boolean;
    }
  | { type: "page"; page: PeoplePage }
  | { type: "openGroup"; ownerId: string; groupKey?: string }
  | { type: "rankFilter"; ownerId: string; rank?: PositionRank }
  | { type: "focus"; id: string }
  | { type: "prune"; id: string }
  /** Collapse a previously-expanded node: keep the node itself, but drop any
   *  neighbours (and their downstream descendants) that were only visible
   *  because of this node's expansion. Trail entries and other explicitly-
   *  expanded nodes always survive. */
  | { type: "collapse"; id: string }
  /** Rewind the trail to `index` and select `id` there, pruning downstream
   *  branches that are no longer reachable from the new trail. */
  | { type: "trailSelect"; index: number; id: string }
  | { type: "reset"; keepId: string };

export function emptySession(focusId: string): SessionState {
  return {
    nodes: {},
    links: {},
    trail: [],
    focusId,
    loading: {},
    groups: {},
    pages: {},
    step: 0,
  };
}

function relationInto(nodeId: string, focusId: string, links: HopLink[]): Relation | undefined {
  if (nodeId === focusId) return undefined;
  const l = links.find(
    (x) =>
      (x.source === focusId && x.target === nodeId) ||
      (x.target === focusId && x.source === nodeId),
  );
  return l?.relation;
}

export function sessionReducer(state: SessionState, action: SessionAction): SessionState {
  switch (action.type) {
    case "loading":
      return { ...state, loading: { ...state.loading, [action.id]: action.value } };

    case "merge": {
      const step = action.advanceTrail ? state.step + 1 : state.step;
      const nodes = { ...state.nodes };
      for (const n of action.nodes) {
        const prev = nodes[n.id];
        nodes[n.id] = {
          ...n,
          step: prev?.step ?? step,
          expanded: prev?.expanded || n.id === action.focusId,
          via: prev?.via ?? relationInto(n.id, action.focusId, action.links),
        };
      }

      const links = { ...state.links };
      for (const l of action.links) {
        if (!SESSION_RELATIONS.has(l.relation)) continue;
        if (!nodes[l.source] || !nodes[l.target]) continue;
        const key = linkKey(l.source, l.target);
        links[key] = mergeLink(links[key], l);
      }

      const groups = action.groups
        ? {
            ...state.groups,
            [action.focusId]: {
              ownerId: action.focusId,
              groups: action.groups,
              openKey: state.groups[action.focusId]?.openKey,
              rankFilter: state.groups[action.focusId]?.rankFilter,
            },
          }
        : state.groups;

      const pages = action.page
        ? { ...state.pages, [pageKey(action.page.ownerId, action.page.groupKey)]: action.page }
        : state.pages;

      const trail = action.advanceTrail
        ? [...state.trail.filter((t) => t !== action.focusId), action.focusId]
        : state.trail;

      return {
        ...state,
        nodes,
        links,
        groups,
        pages,
        trail,
        step,
        focusId: action.advanceTrail ? action.focusId : state.focusId,
      };
    }

    case "page": {
      const key = pageKey(action.page.ownerId, action.page.groupKey);
      return {
        ...state,
        pages: {
          ...state.pages,
          [key]: mergePage(state.pages[key], action.page),
        },
      };
    }

    case "openGroup": {
      const existing = state.groups[action.ownerId];
      if (!existing) return state;
      return {
        ...state,
        groups: {
          ...state.groups,
          [action.ownerId]: {
            ...existing,
            openKey: action.groupKey,
            // Filter is per-open-cohort; drop it when the cohort collapses.
            rankFilter: action.groupKey ? existing.rankFilter : undefined,
          },
        },
      };
    }

    case "rankFilter": {
      const existing = state.groups[action.ownerId];
      if (!existing) return state;
      return {
        ...state,
        groups: {
          ...state.groups,
          [action.ownerId]: { ...existing, rankFilter: action.rank },
        },
      };
    }

    case "focus":
      return {
        ...state,
        focusId: action.id,
        trail: state.trail.includes(action.id)
          ? state.trail
          : [...state.trail, action.id],
      };

    case "prune": {
      const nodes = { ...state.nodes };
      delete nodes[action.id];
      const links: Record<string, SessionLink> = {};
      for (const [k, l] of Object.entries(state.links)) {
        if (l.source === action.id || l.target === action.id) continue;
        links[k] = l;
      }
      const trail = state.trail.filter((t) => t !== action.id);
      return {
        ...state,
        nodes,
        links,
        trail,
        focusId:
          state.focusId === action.id
            ? (trail[trail.length - 1] ?? state.focusId)
            : state.focusId,
      };
    }

    case "collapse": {
      const P = action.id;
      if (!state.nodes[P]) return state;

      // Anchors are nodes that must survive the collapse regardless of P.
      // We seed the reachability search from anchors OTHER than P through
      // an adjacency graph that omits P's edges — so anything only reachable
      // through P gets pruned.
      const anchors = new Set<string>(state.trail);
      for (const n of Object.values(state.nodes)) {
        if (n.expanded) anchors.add(n.id);
      }

      const adjWithoutP = new Map<string, string[]>();
      const addAdj = (a: string, b: string) => {
        const list = adjWithoutP.get(a);
        if (list) list.push(b);
        else adjWithoutP.set(a, [b]);
      };
      for (const l of Object.values(state.links)) {
        if (l.source === P || l.target === P) continue;
        addAdj(l.source, l.target);
        addAdj(l.target, l.source);
      }

      const kept = new Set<string>([P]);
      const queue: string[] = [];
      for (const a of anchors) {
        if (a === P) continue;
        if (!state.nodes[a]) continue;
        if (!kept.has(a)) {
          kept.add(a);
          queue.push(a);
        }
      }
      for (let head = 0; head < queue.length; head++) {
        const cur = queue[head];
        for (const nb of adjWithoutP.get(cur) ?? []) {
          if (!kept.has(nb)) {
            kept.add(nb);
            queue.push(nb);
          }
        }
      }

      const newNodes: Record<string, SessionNode> = {};
      for (const [id, n] of Object.entries(state.nodes)) {
        if (!kept.has(id)) continue;
        newNodes[id] = id === P ? { ...n, expanded: false } : n;
      }

      const newLinks: Record<string, SessionLink> = {};
      for (const [k, l] of Object.entries(state.links)) {
        if (!kept.has(l.source) || !kept.has(l.target)) continue;
        newLinks[k] = l;
      }

      // Close any cohort we opened for P — the roster shouldn't reopen when
      // the user re-expands later, they should get a fresh choice.
      const newGroups = { ...state.groups };
      if (newGroups[P]) {
        newGroups[P] = { ...newGroups[P], openKey: undefined, rankFilter: undefined };
      }

      const newLoading = { ...state.loading };
      for (const id of Object.keys(newLoading)) {
        // Clear loading flags for anything we just removed so orphaned
        // spinners don't stick around.
        const isNodeKey = state.nodes[id] !== undefined;
        if (isNodeKey && !kept.has(id)) delete newLoading[id];
      }

      const trail = state.trail.filter((t) => kept.has(t));

      return {
        ...state,
        nodes: newNodes,
        links: newLinks,
        trail,
        groups: newGroups,
        loading: newLoading,
      };
    }

    case "trailSelect": {
      const newTrail = [...state.trail.slice(0, action.index), action.id];
      if (newTrail.length === 0 || !state.nodes[action.id]) return state;

      const anchors = new Set<string>(newTrail);
      for (const id of newTrail) {
        const n = state.nodes[id];
        if (n?.expanded) anchors.add(id);
      }

      const adj = new Map<string, string[]>();
      const addAdj = (a: string, b: string) => {
        const list = adj.get(a);
        if (list) list.push(b);
        else adj.set(a, [b]);
      };
      for (const l of Object.values(state.links)) {
        addAdj(l.source, l.target);
        addAdj(l.target, l.source);
      }

      const kept = new Set<string>();
      const queue: string[] = [];
      for (const a of anchors) {
        if (!state.nodes[a]) continue;
        kept.add(a);
        queue.push(a);
      }
      for (let head = 0; head < queue.length; head++) {
        const cur = queue[head];
        for (const nb of adj.get(cur) ?? []) {
          if (!kept.has(nb) && state.nodes[nb]) {
            kept.add(nb);
            queue.push(nb);
          }
        }
      }

      const newNodes: Record<string, SessionNode> = {};
      for (const [id, n] of Object.entries(state.nodes)) {
        if (!kept.has(id)) continue;
        newNodes[id] = n;
      }

      const newLinks: Record<string, SessionLink> = {};
      for (const [k, l] of Object.entries(state.links)) {
        if (!kept.has(l.source) || !kept.has(l.target)) continue;
        newLinks[k] = l;
      }

      const newGroups = { ...state.groups };
      for (const ownerId of Object.keys(newGroups)) {
        if (!kept.has(ownerId)) delete newGroups[ownerId];
      }

      const newPages = { ...state.pages };
      for (const key of Object.keys(newPages)) {
        const [ownerId] = key.split("::");
        if (!kept.has(ownerId)) delete newPages[key];
      }

      const newLoading = { ...state.loading };
      for (const id of Object.keys(newLoading)) {
        if (state.nodes[id] && !kept.has(id)) delete newLoading[id];
      }

      return {
        ...state,
        nodes: newNodes,
        links: newLinks,
        trail: newTrail,
        focusId: action.id,
        groups: newGroups,
        pages: newPages,
        loading: newLoading,
      };
    }

    case "reset":
      return emptySession(action.keepId);

    default:
      return state;
  }
}

// -----------------------------------------------------------------------------
// Derived selectors
// -----------------------------------------------------------------------------

/** Direct neighbours of `nodeId` in the org-hierarchy canvas. */
export function neighborIds(state: SessionState, nodeId: string): string[] {
  const ids = new Set<string>();
  for (const l of Object.values(state.links)) {
    if (l.source === nodeId) ids.add(l.target);
    if (l.target === nodeId) ids.add(l.source);
  }

  const group = state.groups[nodeId];
  if (group?.openKey) {
    const page = state.pages[pageKey(nodeId, group.openKey)];
    for (const item of page?.items ?? []) ids.add(item.id);
  }

  return [...ids]
    .filter((id) => state.nodes[id])
    .sort((a, b) => {
      const la = state.nodes[a]!.label;
      const lb = state.nodes[b]!.label;
      return la.localeCompare(lb);
    });
}

/** BFS distance from the focus. Used to fade nodes further from attention. */
export function distancesFrom(state: SessionState, focusId: string): Map<string, number> {
  const adj = new Map<string, string[]>();
  for (const l of Object.values(state.links)) {
    if (!adj.has(l.source)) adj.set(l.source, []);
    if (!adj.has(l.target)) adj.set(l.target, []);
    adj.get(l.source)!.push(l.target);
    adj.get(l.target)!.push(l.source);
  }
  const dist = new Map<string, number>([[focusId, 0]]);
  const queue = [focusId];
  for (let head = 0; head < queue.length; head++) {
    const cur = queue[head];
    const d = dist.get(cur)!;
    for (const nb of adj.get(cur) ?? []) {
      if (!dist.has(nb)) {
        dist.set(nb, d + 1);
        queue.push(nb);
      }
    }
  }
  return dist;
}
