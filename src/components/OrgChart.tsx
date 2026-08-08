import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useApolloClient } from "@apollo/client/react";
import { CombinedGraphQLErrors } from "@apollo/client";
import {
  EXPAND,
  FETCH_PAGE,
  type ExpandData,
  type ExpandVars,
  type FetchPageData,
  type FetchPageVars,
} from "../api/queries";
import { layoutSession, type RFNodeData } from "../lib/graphLayout";
import {
  emptySession,
  sessionReducer,
  pageKey,
} from "../lib/graphSession";
import type { PositionRank } from "../lib/positionRank";
import { nodeTypes } from "./graphNodes";

interface Props {
  /** Initial or externally-driven focus node. Changing it seeds an expansion
   *  centred on that id but does NOT wipe previously accumulated nodes. */
  focusId: string;
  onFocus?: (id: string) => void;
  minHeight?: number | string;
  className?: string;
}

function OrgChartInner({ focusId, onFocus, minHeight, className }: Props) {
  const { setCenter, getNode } = useReactFlow();
  const client = useApolloClient();
  const [session, dispatch] = useReducer(sessionReducer, focusId, emptySession);
  const inFlight = useRef<Set<string>>(new Set());
  const errorRef = useRef<string | null>(null);

  // ---------------------------------------------------------------------------
  // Every selection triggers a "one hop around this node" fetch and MERGES the
  // result. Previously revealed branches are never dropped, so the graph is
  // the trace of the user's investigation.
  // ---------------------------------------------------------------------------
  const expand = useCallback(
    async (id: string, advanceTrail = true) => {
      if (inFlight.current.has(id)) return;
      inFlight.current.add(id);
      dispatch({ type: "loading", id, value: true });
      try {
        const { data } = await client.query<ExpandData, ExpandVars>({
          query: EXPAND,
          variables: { id },
          fetchPolicy: "cache-first",
        });
        if (!data) throw new Error("expand returned no data");
        const res = data.expand;
        errorRef.current = null;
        dispatch({
          type: "merge",
          focusId: res.focusId,
          nodes: res.nodes,
          links: res.links,
          groups: res.groups,
          page: res.page,
          advanceTrail,
        });

        if (!advanceTrail) return;

        // Post-merge orchestration:
        // - If the new focus is an org whose roster came back as a group, open
        //   it directly so the user sees the list of people first.
        // - If the new focus is a person, close whatever cohort was open. At
        //   most one cohort is ever visible; the peer badge on the person's
        //   card re-opens it on demand.
        const focus = res.nodes.find((n) => n.id === res.focusId);
        if (focus?.kind === "org" && res.groups && res.groups.length > 0) {
          dispatch({
            type: "openGroup",
            ownerId: res.focusId,
            groupKey: res.groups[0].key,
          });
        } else if (focus?.kind === "person") {
          const anchor = res.links.find(
            (l) => l.relation === "placement" && l.target === focus.id,
          );
          if (anchor) {
            dispatch({
              type: "openGroup",
              ownerId: anchor.source,
              groupKey: undefined,
            });
          }
        }
      } catch (err) {
        const msg = describeError(err);
        errorRef.current = msg;
        console.error("[OrgChart] expand failed", err);
      } finally {
        inFlight.current.delete(id);
        dispatch({ type: "loading", id, value: false });
      }
    },
    [client],
  );

  // Seed on first mount and whenever the caller changes the focus prop
  // (e.g. via search, breadcrumbs, or the URL). If the node is already
  // expanded we just re-centre; otherwise we fetch its neighbourhood.
  useEffect(() => {
    if (session.nodes[focusId]?.expanded) {
      dispatch({ type: "focus", id: focusId });
      return;
    }
    void expand(focusId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);

  // Notify caller when the internal focus changes (a user clicked a new node).
  useEffect(() => {
    if (onFocus && session.focusId !== focusId) onFocus(session.focusId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.focusId]);

  // Selection is a toggle:
  //   • click a node not yet expanded  → fetch its 1-hop and focus it
  //   • click a node already expanded and already focused → collapse it
  //     (prune everything that was only visible through this node)
  //   • click an expanded node that isn't focused → just move focus, no fetch
  const onSelect = useCallback(
    (id: string) => {
      const node = session.nodes[id];
      const isFocus = id === session.focusId;
      if (node?.expanded && isFocus) {
        dispatch({ type: "collapse", id });
        return;
      }
      if (node?.expanded) {
        dispatch({ type: "focus", id });
        return;
      }
      void expand(id);
    },
    [expand, session.focusId, session.nodes],
  );

  const fetchPage = useCallback(
    async (ownerId: string, groupKey: string, offset: number) => {
      const { data } = await client.query<FetchPageData, FetchPageVars>({
        query: FETCH_PAGE,
        variables: { ownerId, groupKey, offset },
        fetchPolicy: "cache-first",
      });
      if (!data) throw new Error("pages returned no data");
      return data.pages;
    },
    [client],
  );

  const onToggleGroup = useCallback(
    async (ownerId: string, key: string) => {
      const openKey = session.groups[ownerId]?.openKey;
      if (openKey === key) {
        dispatch({ type: "openGroup", ownerId, groupKey: undefined });
        return;
      }
      dispatch({ type: "openGroup", ownerId, groupKey: key });
      if (session.pages[pageKey(ownerId, key)]) return;
      const gid = `grp:${ownerId}:${key}`;
      dispatch({ type: "loading", id: gid, value: true });
      try {
        const page = await fetchPage(ownerId, key, 0);
        dispatch({ type: "page", page });
      } catch (err) {
        console.error("[OrgChart] fetchPage failed", err);
      } finally {
        dispatch({ type: "loading", id: gid, value: false });
      }
    },
    [fetchPage, session.groups, session.pages],
  );

  const onLoadMore = useCallback(
    async (ownerId: string, key: string) => {
      const existing = session.pages[pageKey(ownerId, key)];
      const bandId = `band:${ownerId}:${key}`;
      dispatch({ type: "loading", id: bandId, value: true });
      try {
        const page = await fetchPage(ownerId, key, existing?.offset ?? 0);
        dispatch({ type: "page", page });
      } catch (err) {
        console.error("[OrgChart] fetchPage failed", err);
      } finally {
        dispatch({ type: "loading", id: bandId, value: false });
      }
    },
    [fetchPage, session.pages],
  );

  const onFilter = useCallback(
    (ownerId: string, rank?: PositionRank) => {
      dispatch({ type: "rankFilter", ownerId, rank });
    },
    [],
  );

  const { nodes, edges, stats } = useMemo(
    () =>
      layoutSession(session, {
        onSelect,
        onToggleGroup,
        onLoadMore,
        onFilter,
      }),
    [session, onSelect, onToggleGroup, onLoadMore, onFilter],
  );

  // Gentle re-centre on focus. Never a hard fit — reflowing on every click
  // would jump the viewport while the user is reading it.
  useEffect(() => {
    const t = window.setTimeout(() => {
      const n = getNode(session.focusId);
      if (!n) return;
      const w = n.measured?.width ?? 216;
      const h = n.measured?.height ?? 72;
      setCenter(n.position.x + w / 2, n.position.y + h / 2, {
        zoom: 0.95,
        duration: 380,
      });
    }, 90);
    return () => window.clearTimeout(t);
  }, [session.focusId, nodes.length, getNode, setCenter]);

  const anyLoading = Object.values(session.loading).some(Boolean);

  return (
    <div
      className={`org-chart ${className ?? ""}`.trim()}
      style={{ minHeight: minHeight ?? 560 }}
      data-testid="org-chart"
    >
      <ReactFlow
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        nodes={nodes as any}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.15}
        maxZoom={1.8}
        nodesDraggable={false}
        nodesConnectable={false}
        panOnScroll
        zoomOnScroll
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={16}
          size={1}
          color="rgba(99, 102, 241, 0.12)"
        />
        <Controls
          showInteractive={false}
          className="org-chart__controls"
        />
        <MiniMap
          nodeStrokeColor={(n) =>
            n.id === session.focusId ? "#6366f1" : "rgba(99, 102, 241, 0.15)"
          }
          nodeColor={(n) => {
            if (n.id === session.focusId) return "#6366f1";
            if (n.type === "cohort") return "#e0e7ff";
            if (n.type === "person") return "#ffffff";
            return "#f4f4f5";
          }}
          maskColor="rgba(250, 250, 250, 0.85)"
          className="org-chart__minimap"
          pannable
          zoomable
        />
      </ReactFlow>

      <div className="org-chart__status">
        {anyLoading ? (
          <span className="org-chart__status-dot org-chart__status-dot--loading" />
        ) : (
          <span className="org-chart__status-dot" />
        )}
        {anyLoading ? "loading…" : `${stats.nodes} nodes`}
        {stats.folded > 0 && <span> · {stats.folded} folded</span>}
        {errorRef.current && (
          <span className="org-chart__error"> · {errorRef.current}</span>
        )}
      </div>
    </div>
  );
}

// Silence the type-only reference to RFNodeData so it doesn't get tree-shaken
// away by lint.
export type _OrgChartNodeData = RFNodeData;

function describeError(err: unknown): string {
  if (err instanceof CombinedGraphQLErrors) {
    return err.errors.map((e) => e.message).join(" · ") || "graphql error";
  }
  if (err instanceof Error) return err.message;
  return "unknown error";
}

export function OrgChart(props: Props) {
  return (
    <ReactFlowProvider>
      <OrgChartInner {...props} />
    </ReactFlowProvider>
  );
}
