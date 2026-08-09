import { useEffect, useMemo } from "react";
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
import { layoutSession, type RFNodeData } from "../lib/graphLayout";
import type { GraphSessionApi } from "../lib/useGraphSession";
import { useGraphSession } from "../lib/useGraphSession";
import { nodeTypes } from "./graphNodes";

type SessionApi = Pick<
  GraphSessionApi,
  "session" | "onSelect" | "onToggleGroup" | "onLoadMore" | "onFilter" | "error"
>;

interface ControlledProps {
  focusId: string;
  session: SessionApi["session"];
  api: Pick<
    GraphSessionApi,
    "onSelect" | "onToggleGroup" | "onLoadMore" | "onFilter"
  >;
  error?: string | null;
  minHeight?: number | string;
  className?: string;
}

interface UncontrolledProps {
  focusId: string;
  onFocus?: (id: string) => void;
  minHeight?: number | string;
  className?: string;
  session?: never;
  api?: never;
  error?: never;
}

type Props = ControlledProps | UncontrolledProps;

function OrgChartCanvas({
  session,
  api,
  error,
  minHeight,
  className,
}: {
  session: SessionApi["session"];
  api: Pick<
    GraphSessionApi,
    "onSelect" | "onToggleGroup" | "onLoadMore" | "onFilter"
  >;
  error?: string | null;
  minHeight?: number | string;
  className?: string;
}) {
  const { setCenter, getNode } = useReactFlow();
  const { onSelect, onToggleGroup, onLoadMore, onFilter } = api;

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

  useEffect(() => {
    const t = window.setTimeout(() => {
      const n = getNode(session.focusId);
      if (!n) return;
      const w = n.measured?.width ?? 180;
      const h = n.measured?.height ?? 68;
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
        {error && (
          <span className="org-chart__error"> · {error}</span>
        )}
      </div>
    </div>
  );
}

function OrgChartControlled(props: ControlledProps) {
  return (
    <OrgChartCanvas
      session={props.session}
      api={props.api}
      error={props.error}
      minHeight={props.minHeight}
      className={props.className}
    />
  );
}

function OrgChartUncontrolled({
  focusId,
  onFocus,
  minHeight,
  className,
}: UncontrolledProps) {
  const graphSession = useGraphSession(focusId, onFocus);
  return (
    <OrgChartCanvas
      session={graphSession.session}
      api={graphSession}
      error={graphSession.error}
      minHeight={minHeight}
      className={className}
    />
  );
}

export type _OrgChartNodeData = RFNodeData;

export function OrgChart(props: Props) {
  return (
    <ReactFlowProvider>
      {"session" in props && props.session ? (
        <OrgChartControlled {...props} />
      ) : (
        <OrgChartUncontrolled {...props} />
      )}
    </ReactFlowProvider>
  );
}
