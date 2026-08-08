import dagre from "dagre";
import type { Edge, Node } from "@xyflow/react";
import type { PositionRank } from "./positionRank";
import {
  distancesFrom,
  type PeoplePage,
  type Relation,
  type SessionLink,
  type SessionState,
} from "./graphSession";

// -----------------------------------------------------------------------------
// Layout
//
// Pure. Input: session state + callbacks. Output: React Flow shapes.
//
// Dagre computes rank (depth) and order-in-rank for every node globally, which
// is what avoids the pathology of the old custom layout — long crossing edges
// through unrelated cards. Every render re-solves the whole layout, so the
// tree rebalances on each click without any special "rebalance" step.
// -----------------------------------------------------------------------------

// Must stay in sync with `.graph-node`, `.graph-node--org`, `.graph-peer`,
// and `.graph-cohort` in App.css. Dagre reserves exactly these dimensions,
// so if CSS ever grows the card it will overlap its siblings.
const SIZES: Record<string, { w: number; h: number }> = {
  person: { w: 216, h: 76 },
  org: { w: 228, h: 84 },
  cohort: { w: 880, h: 220 }, // grows further in sizeOf if the band is long
  peer_more: { w: 74, h: 44 },
};

export type NodeType = "person" | "org" | "cohort" | "peer_more";

// A wide cohort band that lists people paginated. Attached directly to its
// owning org — the intermediate "group chip" step has been removed so that
// focusing an org shows its roster immediately.
export interface CohortData extends Record<string, unknown> {
  kind: "cohort";
  ownerId: string;
  groupKey: string;
  parentName: string;
  groupLabel: string;
  count: number;
  people: Array<{
    id: string;
    name: string;
    role?: string;
    rank?: PositionRank;
    selected: boolean;
    onCanvas: boolean;
  }>;
  ranks: PositionRank[];
  rankFilter?: PositionRank;
  remaining: number;
  pageStep: number;
  fade: number;
  loading: boolean;
  onLoadMore: () => void;
  onToggle: () => void;
  onFilter: (rank?: PositionRank) => void;
  onPersonSelect: (id: string) => void;
}

export interface PersonNodeData extends Record<string, unknown> {
  kind: "person";
  name: string;
  role?: string;
  institution?: string;
  via?: Relation;
  selected: boolean;
  onPath: boolean;
  fade: number;
  stub?: boolean;
  loading: boolean;
  onSelect: () => void;
}

/** A small peer-slot node placed at the same rank as a person, hanging off
 *  the same anchor org. Clicking re-opens the org's cohort band. */
export interface PeerMoreData extends Record<string, unknown> {
  kind: "peer_more";
  count: number;
  ownerId: string;
  fade: number;
  onOpen: () => void;
}

export interface OrgNodeData extends Record<string, unknown> {
  kind: "org";
  name: string;
  sublabel?: string;
  orgKind?: string;
  selected: boolean;
  onPath: boolean;
  fade: number;
  loading: boolean;
  onSelect: () => void;
}

export type RFNodeData =
  | PersonNodeData
  | OrgNodeData
  | CohortData
  | PeerMoreData;

export interface LayoutCallbacks {
  onSelect: (id: string) => void;
  onToggleGroup: (ownerId: string, groupKey: string) => void;
  onLoadMore: (ownerId: string, groupKey: string) => void;
  onFilter: (ownerId: string, rank?: PositionRank) => void;
}

function sizeOf(type: NodeType, data: RFNodeData): { w: number; h: number } {
  if (type === "cohort") {
    const d = data as CohortData;
    const cols = 4;
    const rows = Math.ceil(d.people.length / cols);
    const listH = Math.min(360, Math.max(56, rows * 44));
    const footer = d.remaining > 0 ? 46 : 0;
    const filterBar = d.ranks.length >= 2 ? 44 : 0;
    return { w: 880, h: 64 + filterBar + listH + footer };
  }
  return SIZES[type as keyof typeof SIZES] ?? SIZES.org;
}

const RELATION_STYLE: Record<
  Relation,
  { stroke: string; width: number; dash?: string }
> = {
  report: { stroke: "#8a5a3b", width: 2 },
  placement: { stroke: "#2f6a5e", width: 2 },
  org_parent: { stroke: "#2f6a5e", width: 2 },
  coauthor: { stroke: "#cf5a2b", width: 1.75, dash: "5 4" },
};

export interface LayoutStats {
  nodes: number;
  folded: number;
}

function nodeBox(
  id: string,
  g: dagre.graphlib.Graph,
  gnodes: Array<{ id: string; type: NodeType; data: RFNodeData }>,
): { left: number; top: number; right: number; bottom: number; w: number; h: number } | null {
  const pos = g.node(id);
  const gn = gnodes.find((n) => n.id === id);
  if (!pos || !gn) return null;
  const { w, h } = sizeOf(gn.type, gn.data);
  return {
    left: pos.x - w / 2,
    top: pos.y - h / 2,
    right: pos.x + w / 2,
    bottom: pos.y + h / 2,
    w,
    h,
  };
}

function boxesOverlap(
  a: { left: number; top: number; right: number; bottom: number },
  b: { left: number; top: number; right: number; bottom: number },
  gap: number,
): boolean {
  return (
    a.left - gap < b.right &&
    a.right + gap > b.left &&
    a.top - gap < b.bottom &&
    a.bottom + gap > b.top
  );
}

/** Push peer right (then down as fallback) until it clears every other node. */
function resolvePeerCollision(
  peerId: string,
  cx: number,
  cy: number,
  g: dagre.graphlib.Graph,
  gnodes: Array<{ id: string; type: NodeType; data: RFNodeData }>,
  gap = 8,
): { x: number; y: number } {
  const peerGn = gnodes.find((n) => n.id === peerId);
  if (!peerGn) return { x: cx, y: cy };
  const { w: peerW, h: peerH } = sizeOf(peerGn.type, peerGn.data);

  const others = gnodes
    .filter((n) => n.id !== peerId)
    .map((n) => nodeBox(n.id, g, gnodes))
    .filter((b): b is NonNullable<typeof b> => b != null);

  let x = cx;
  let y = cy;
  const peerBand = () => ({
    left: x - peerW / 2,
    top: y - peerH / 2,
    right: x + peerW / 2,
    bottom: y + peerH / 2,
  });

  for (let iter = 0; iter < 80; iter++) {
    const pb = peerBand();
    const hit = others.find((ob) => boxesOverlap(pb, ob, gap));
    if (!hit) break;
    const nextX = hit.right + gap + peerW / 2;
    if (nextX <= x) {
      y = hit.bottom + gap + peerH / 2;
    } else {
      x = nextX;
    }
  }

  return { x, y };
}

export function layoutSession(
  state: SessionState,
  cb: LayoutCallbacks,
): { nodes: Node<RFNodeData>[]; edges: Edge[]; stats: LayoutStats } {
  const dist = distancesFrom(state, state.focusId);
  const gnodes: Array<{ id: string; type: NodeType; data: RFNodeData }> = [];
  const structural: SessionLink[] = [];
  let folded = 0;

  // --- Entity nodes ---------------------------------------------------------
  for (const n of Object.values(state.nodes)) {
    const d = dist.get(n.id);
    const fade =
      d === undefined ? 0.45 : d <= 1 ? 0 : d === 2 ? 0.12 : d === 3 ? 0.28 : 0.5;
    const isFocus = n.id === state.focusId;

    if (n.kind === "person") {
      gnodes.push({
        id: n.id,
        type: "person",
        data: {
          kind: "person",
          name: n.label,
          role: n.sublabel,
          institution: n.institution,
          via: n.via,
          selected: isFocus,
          onPath: (d ?? 9) <= 1,
          fade,
          stub: n.stub,
          loading: Boolean(state.loading[n.id]),
          onSelect: () => cb.onSelect(n.id),
        },
      });
    } else {
      gnodes.push({
        id: n.id,
        type: "org",
        data: {
          kind: "org",
          name: n.label,
          sublabel: n.sublabel,
          orgKind: n.orgKind,
          selected: isFocus,
          onPath: (d ?? 9) <= 1,
          fade,
          loading: Boolean(state.loading[n.id]),
          onSelect: () => cb.onSelect(n.id),
        },
      });
    }
  }

  for (const l of Object.values(state.links)) {
    if (state.nodes[l.source] && state.nodes[l.target]) structural.push(l);
  }

  // --- Peer-slot nodes -----------------------------------------------------
  //
  // Rendered when a person's anchor org has a summarised roster whose cohort
  // band is currently CLOSED. Exactly ONE peer-slot node per anchor org, no
  // matter how many of its people are already on the canvas. The count
  // reflects how many colleagues are still hidden.
  //
  // Keyed by org id alone (`peer:${orgId}`) so successive clicks on people
  // from the same roster reuse the same layout slot — they don't spawn a
  // fresh "+N more" node for each individual.
  const groupEdges: Array<{ source: string; target: string }> = [];
  const peerAlign: Array<{ peerId: string; anchorOrgId: string }> = [];

  const anchoredPersonsByOrg = new Map<string, string[]>();
  for (const l of structural) {
    if (l.relation !== "placement") continue;
    if (state.nodes[l.target]?.kind !== "person") continue;
    const list = anchoredPersonsByOrg.get(l.source) ?? [];
    list.push(l.target);
    anchoredPersonsByOrg.set(l.source, list);
  }

  for (const [anchorOrgId, persons] of anchoredPersonsByOrg) {
    const onCanvas = persons.length;
    if (!state.nodes[anchorOrgId]) continue;
    const anchorGroups = state.groups[anchorOrgId];
    if (!anchorGroups || anchorGroups.openKey != null) continue;
    const group = anchorGroups.groups[0];
    if (!group) continue;
    const remaining = group.count - onCanvas;
    if (remaining <= 0) continue;

    const peerId = `peer:${anchorOrgId}`;
    const ownerDist = dist.get(anchorOrgId) ?? 9;
    const peerFade =
      ownerDist <= 1 ? 0 : ownerDist === 2 ? 0.15 : 0.35;

    gnodes.push({
      id: peerId,
      type: "peer_more",
      data: {
        kind: "peer_more",
        count: remaining,
        ownerId: anchorOrgId,
        fade: peerFade,
        onOpen: () => cb.onToggleGroup(anchorOrgId, group.key),
      },
    });
    // Routed from the anchor org (e.g. GSB), not chained off a person card.
    peerAlign.push({ peerId, anchorOrgId });
  }

  // --- Open cohort bands ---------------------------------------------------
  //
  // The intermediate group chip is gone. The cohort attaches directly to its
  // owning org and is only rendered when that group is `open` — closing it
  // just removes it from the layout.
  for (const gs of Object.values(state.groups)) {
    if (!state.nodes[gs.ownerId]) continue;
    const ownerDist = dist.get(gs.ownerId) ?? 9;
    const fade = ownerDist <= 1 ? 0 : ownerDist === 2 ? 0.15 : 0.35;

    for (const g of gs.groups) {
      const open = gs.openKey === g.key;
      if (!open) {
        folded += g.count;
        continue;
      }

      const page: PeoplePage | undefined =
        state.pages[`${gs.ownerId}::${g.key}`];
      const bandId = `band:${gs.ownerId}:${g.key}`;
      const raw = page?.items ?? [];
      const total = page?.total ?? g.count;

      const rankSet = new Set<PositionRank>();
      for (const item of raw) if (item.rank) rankSet.add(item.rank);
      const ranks = [...rankSet];

      const filtered = gs.rankFilter
        ? raw.filter((p) => p.rank === gs.rankFilter)
        : raw;

      const remaining = page ? total - raw.length : g.count;
      if (remaining > 0) folded += remaining;

      gnodes.push({
        id: bandId,
        type: "cohort",
        data: {
          kind: "cohort",
          ownerId: gs.ownerId,
          groupKey: g.key,
          parentName: state.nodes[gs.ownerId]?.label ?? "",
          groupLabel: g.label,
          count: total,
          people: filtered.map((p) => ({
            id: p.id,
            name: p.label,
            role: p.sublabel,
            rank: p.rank,
            selected: p.id === state.focusId,
            onCanvas: Boolean(state.nodes[p.id]),
          })),
          ranks,
          rankFilter: gs.rankFilter,
          remaining,
          pageStep: raw.length,
          fade,
          loading: Boolean(state.loading[bandId]),
          onLoadMore: () => cb.onLoadMore(gs.ownerId, g.key),
          onToggle: () => cb.onToggleGroup(gs.ownerId, g.key),
          onFilter: (rank) => cb.onFilter(gs.ownerId, rank),
          onPersonSelect: (pid: string) => cb.onSelect(pid),
        },
      });
      groupEdges.push({ source: gs.ownerId, target: bandId });
    }
  }

  // --- Dagre ---------------------------------------------------------------
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  // `nodesep` is the min horizontal gap between siblings at the same rank.
  // Bumping to 52 leaves comfortable breathing room around the 216/228px
  // cards even when neighbours are visually loud (bold labels, dashed
  // borders). `edgesep` widens the gutters dagre keeps between parallel
  // edges so the T-junction crossbar doesn't crowd sibling cards.
  g.setGraph({
    rankdir: "TB",
    nodesep: 52,
    ranksep: 108,
    marginx: 48,
    marginy: 48,
    edgesep: 24,
  });

  for (const n of gnodes) {
    const { w, h } = sizeOf(n.type, n.data);
    g.setNode(n.id, { width: w, height: h });
  }
  for (const l of structural) g.setEdge(l.source, l.target);
  for (const e of groupEdges) g.setEdge(e.source, e.target);
  dagre.layout(g);

  // Snap "+N" to the person row under the anchor org. Top-align with person
  // cards so sibling step edges from the org share one crossbar. When no
  // placement colleagues are on canvas yet, estimate the row from the anchor
  // org. Always run collision resolution — peers are not in the dagre edge
  // graph so they otherwise sit at (0,0) and overlap real nodes.
  const NODESEP = 52;
  const RANKSEP = 108;
  const peerEdges: Array<{ source: string; target: string }> = [];
  for (const { peerId, anchorOrgId } of peerAlign) {
    const persons = anchoredPersonsByOrg.get(anchorOrgId) ?? [];
    const peerPos = g.node(peerId);
    if (!peerPos) continue;
    const peerW = SIZES.peer_more.w;
    const peerH = SIZES.peer_more.h;

    let alignTop = Infinity;
    let maxRight = -Infinity;

    for (const pid of persons) {
      const pos = g.node(pid);
      if (!pos) continue;
      const { w, h } = sizeOf("person", gnodes.find((n) => n.id === pid)!.data);
      maxRight = Math.max(maxRight, pos.x + w / 2);
      alignTop = Math.min(alignTop, pos.y - h / 2);
    }

    const anchorGn = gnodes.find((n) => n.id === anchorOrgId);
    const anchorPos = g.node(anchorOrgId);
    if (anchorPos && anchorGn) {
      const { w: anchorW, h: anchorH } = sizeOf("org", anchorGn.data);
      maxRight = Math.max(maxRight, anchorPos.x + anchorW / 2);
      if (alignTop === Infinity) {
        alignTop =
          anchorPos.y + anchorH / 2 + RANKSEP - SIZES.person.h / 2;
      }
    }

    if (alignTop === Infinity) continue;

    const rowBottom = alignTop + Math.max(peerH, SIZES.person.h);
    for (const gn of gnodes) {
      if (gn.id === peerId) continue;
      const box = nodeBox(gn.id, g, gnodes);
      if (!box) continue;
      if (box.top < rowBottom + 8 && box.bottom > alignTop - 8) {
        maxRight = Math.max(maxRight, box.right);
      }
    }

    const desiredX = maxRight + NODESEP + peerW / 2;
    const desiredY = alignTop + peerH / 2;
    const resolved = resolvePeerCollision(peerId, desiredX, desiredY, g, gnodes);
    peerPos.x = resolved.x;
    peerPos.y = resolved.y;
    peerEdges.push({ source: anchorOrgId, target: peerId });
  }

  const nodes: Node<RFNodeData>[] = [];
  for (const n of gnodes) {
    const pos = g.node(n.id);
    if (!pos) continue;
    const { w, h } = sizeOf(n.type, n.data);
    nodes.push({
      id: n.id,
      type: n.type,
      position: { x: pos.x - w / 2, y: pos.y - h / 2 },
      data: n.data,
      draggable: false,
    });
  }

  // --- Edges: exactly one per pair (session already canonicalised) --------
  //
  // We use `step` (sharp orthogonal corners) rather than `smoothstep` because
  // multiple sibling edges from the same parent naturally overlap on the
  // shared vertical trunk: every step edge draws `(parent_x, parent_bottom) →
  // (parent_x, midY) → (target_x, midY) → (target_x, target_top)`. The first
  // segment is identical across all siblings, so it visually renders as ONE
  // trunk that only splits at the crossbar — no fanned/kinked corners you
  // otherwise get from `smoothstep`'s per-edge rounded arcs.
  const edges: Edge[] = [];
  for (const l of structural) {
    const style = RELATION_STYLE[l.relation];
    const near = (dist.get(l.source) ?? 9) <= 1 || (dist.get(l.target) ?? 9) <= 1;
    const stubEndpoint =
      state.nodes[l.source]?.stub || state.nodes[l.target]?.stub;
    edges.push({
      id: l.key,
      source: l.source,
      target: l.target,
      type: "step",
      style: {
        stroke: style.stroke,
        strokeWidth: style.width,
        strokeDasharray: stubEndpoint ? "4 4" : style.dash,
        opacity: near ? 1 : 0.4,
      },
      label: near ? l.label : undefined,
      labelStyle: { fill: style.stroke, fontSize: 10 },
      labelBgStyle: { fill: "#fbf7ec", fillOpacity: 0.92 },
      labelBgPadding: [4, 2],
      labelBgBorderRadius: 4,
      markerEnd:
        l.relation === "report"
          ? { type: "arrowclosed" as const, color: style.stroke, width: 13, height: 13 }
          : undefined,
    } as Edge);
  }
  for (const e of groupEdges) {
    edges.push({
      id: `g:${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      type: "step",
      style: { stroke: "#1b3a3833", strokeWidth: 1.25 },
    } as Edge);
  }
  for (const e of peerEdges) {
    edges.push({
      id: `peer:${e.source}->${e.target}`,
      source: e.source,
      target: e.target,
      type: "step",
      style: { stroke: "#1b3a3833", strokeWidth: 1.25 },
    } as Edge);
  }

  return { nodes, edges, stats: { nodes: nodes.length, folded } };
}
