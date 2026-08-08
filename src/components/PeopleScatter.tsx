/**
 * PeopleScatter — connected graph of projected people.
 *
 * Points come from the active `embedding_runs` row; edges match the layout
 * graph. Node size scales with impact. Pan/zoom via SVG transform.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@apollo/client/react";
import type {
  ProjectionData,
  ProjectionEdge,
  ProjectionPoint,
} from "../api/projection";
import { PROJECTION } from "../api/projection";

interface Props {
  focusId: string | null;
  /** Focus a person or institution in the tree pane. */
  onFocus: (id: string) => void;
  minHeight?: number | string;
  className?: string;
}

interface Transform {
  tx: number;
  ty: number;
  scale: number;
}

const IDENTITY: Transform = { tx: 0, ty: 0, scale: 1 };
const MIN_POINT_R = 1.5;
const MAX_POINT_R = 4.8;
const FOCUS_R = 6;
const PAD = 24;
const WHEEL_STEP = 0.0018;
/** Zoom limits as multiples of the overview (fit-all) scale. */
const ZOOM_MIN_FACTOR = 0.25;
const ZOOM_MAX_FACTOR = 6;
/** World-space radius around a focused person — wide enough to show context. */
const FOCUS_WORLD_RADIUS = 0.55;
/** At most this many times closer than the overview when focusing a person. */
const FOCUS_ZOOM_FACTOR = 2.2;
/** Scatter points farther than this (world units) from focus are greyed out. */
const NEAR_RADIUS = FOCUS_WORLD_RADIUS;
const FOCUS_COLOR = "#6366f1";
const FOCUS_EDGE_COLOR = "rgba(99, 102, 241, 0.32)";
const FAR_POINT_COLOR = "#d4d4d8";
const EDGE_COLOR = "rgba(99, 102, 241, 0.07)";

/** Deterministic hue in [0, 360). Same input → same colour. */
function hashHue(seed: string | null): number {
  if (!seed) return 210;
  let h = 0;
  for (let i = 0; i < seed.length; i += 1) {
    h = (h * 31 + seed.charCodeAt(i)) & 0xffffff;
  }
  return h % 360;
}

function colourForInstitution(institutionId: string | null): string {
  const hue = hashHue(institutionId);
  return `hsl(${hue}, 52%, 54%)`;
}

function edgeKey(edge: ProjectionEdge): string {
  return edge.sourceId < edge.targetId
    ? `${edge.sourceId}|${edge.targetId}`
    : `${edge.targetId}|${edge.sourceId}`;
}

/** Map normalized impact [0, 1] → screen-space radius (before zoom compensation). */
function radiusForImpact(impact: number): number {
  const t = Math.pow(Math.max(0, Math.min(1, impact)), 0.9);
  return MIN_POINT_R + t * (MAX_POINT_R - MIN_POINT_R);
}

function dist2(
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return dx * dx + dy * dy;
}

/** World-space bbox of a point cloud. */
function bboxOf(points: ProjectionPoint[]): {
  xMin: number;
  yMin: number;
  xMax: number;
  yMax: number;
} {
  if (points.length === 0) {
    return { xMin: -1, yMin: -1, xMax: 1, yMax: 1 };
  }
  let xMin = Infinity;
  let yMin = Infinity;
  let xMax = -Infinity;
  let yMax = -Infinity;
  for (const p of points) {
    if (p.x < xMin) xMin = p.x;
    if (p.y < yMin) yMin = p.y;
    if (p.x > xMax) xMax = p.x;
    if (p.y > yMax) yMax = p.y;
  }
  if (xMin === xMax) {
    xMin -= 1;
    xMax += 1;
  }
  if (yMin === yMax) {
    yMin -= 1;
    yMax += 1;
  }
  return { xMin, yMin, xMax, yMax };
}

function fitAllTransform(
  points: ProjectionPoint[],
  size: { w: number; h: number },
): Transform {
  const { xMin, yMin, xMax, yMax } = bboxOf(points);
  const worldW = xMax - xMin;
  const worldH = yMax - yMin;
  const scale = Math.min(
    (size.w - 2 * PAD) / worldW,
    (size.h - 2 * PAD) / worldH,
  );
  const cx = (xMin + xMax) / 2;
  const cy = (yMin + yMax) / 2;
  return {
    tx: size.w / 2 - scale * cx,
    ty: size.h / 2 + scale * cy,
    scale,
  };
}

/** Centre on a person with a comfortable neighbourhood — not a tight bbox. */
function focusPersonTransform(
  focus: ProjectionPoint,
  points: ProjectionPoint[],
  size: { w: number; h: number },
): Transform {
  const overview = fitAllTransform(points, size);
  const ideal = Math.min(
    (size.w - 2 * PAD) / (2 * FOCUS_WORLD_RADIUS),
    (size.h - 2 * PAD) / (2 * FOCUS_WORLD_RADIUS),
  );
  // Zoom in modestly; never magnify overlapping data into a single dot.
  const scale = Math.min(ideal, overview.scale * FOCUS_ZOOM_FACTOR);
  return {
    tx: size.w / 2 - scale * focus.x,
    ty: size.h / 2 + scale * focus.y,
    scale,
  };
}

export function PeopleScatter({ focusId, onFocus, minHeight, className }: Props) {
  const { data, loading, error } = useQuery<ProjectionData>(PROJECTION, {
    fetchPolicy: "cache-first",
  });
  const points = data?.projection.points ?? [];
  const edges = data?.projection.edges ?? [];
  const runId = data?.projection.runId;

  const pointById = useMemo(() => {
    const map = new Map<string, ProjectionPoint>();
    for (const p of points) map.set(p.id, p);
    return map;
  }, [points]);

  const { degreeById } = useMemo(() => {
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.sourceId, (degree.get(e.sourceId) ?? 0) + 1);
      degree.set(e.targetId, (degree.get(e.targetId) ?? 0) + 1);
    }
    return { degreeById: degree };
  }, [edges]);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 480, h: 480 });
  const [transform, setTransform] = useState<Transform>(IDENTITY);
  const [hovered, setHovered] = useState<ProjectionPoint | null>(null);
  const drag = useRef<{
    startX: number;
    startY: number;
    origin: Transform;
  } | null>(null);
  const cameraKey = useRef<string | null>(null);
  const userPanned = useRef(false);
  const overviewScale = useRef(1);

  const personFocus =
    focusId?.startsWith("p:") === true ? focusId : null;
  const cameraTargetKey = runId
    ? personFocus
      ? `focus:${personFocus}:${size.w}x${size.h}`
      : `fit:${runId}:${size.w}x${size.h}`
    : null;

  // Track container size for the SVG viewBox.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      const w = Math.max(200, rect.width);
      const h = Math.max(200, rect.height);
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Single camera controller — overview or person focus. Re-run when the
  // pane resizes so we never keep a transform computed at 480×480.
  useEffect(() => {
    if (!cameraTargetKey || points.length === 0) return;
    if (userPanned.current) return;
    if (cameraKey.current === cameraTargetKey) return;

    if (personFocus) {
      const point = points.find((p) => p.id === personFocus);
      if (!point) return;
      setTransform(focusPersonTransform(point, points, size));
    } else {
      setTransform(fitAllTransform(points, size));
    }
    cameraKey.current = cameraTargetKey;
  }, [cameraTargetKey, personFocus, points, size]);

  // Reset manual pan when focus or projection run changes.
  useEffect(() => {
    userPanned.current = false;
    cameraKey.current = null;
  }, [personFocus, runId]);

  // Keep overview scale current for wheel-zoom clamping.
  useEffect(() => {
    if (points.length === 0) return;
    overviewScale.current = fitAllTransform(points, size).scale;
  }, [points, size]);

  // Non-passive wheel listener — React's onWheel can't preventDefault, so
  // trackpad pinch/scroll would bubble and zoom the page instead.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      userPanned.current = true;

      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * size.w;
      const py = ((e.clientY - rect.top) / rect.height) * size.h;

      let delta = e.deltaY;
      if (e.deltaMode === WheelEvent.DOM_DELTA_LINE) delta *= 16;
      if (e.deltaMode === WheelEvent.DOM_DELTA_PAGE) delta *= 100;
      // Trackpad pinch-zoom arrives as ctrl+wheel; boost slightly for feel.
      const step = e.ctrlKey ? WHEEL_STEP * 2.5 : WHEEL_STEP;

      const base = overviewScale.current;
      const minScale = base * ZOOM_MIN_FACTOR;
      const maxScale = base * ZOOM_MAX_FACTOR;

      setTransform((t) => {
        const raw = t.scale * Math.exp(-delta * step);
        const scale = Math.max(minScale, Math.min(maxScale, raw));
        const factor = scale / t.scale;
        return {
          tx: px - (px - t.tx) * factor,
          ty: py - (py - t.ty) * factor,
          scale,
        };
      });
    };

    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [size.w, size.h]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if ((e.target as Element).closest("[data-scatter-point]")) return;
      (e.target as Element).setPointerCapture?.(e.pointerId);
      drag.current = {
        startX: e.clientX,
        startY: e.clientY,
        origin: transform,
      };
    },
    [transform],
  );

  const onPointerMove = useCallback((e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    userPanned.current = true;
    setTransform({
      tx: d.origin.tx + (e.clientX - d.startX),
      ty: d.origin.ty + (e.clientY - d.startY),
      scale: d.origin.scale,
    });
  }, []);

  const endDrag = useCallback(() => {
    drag.current = null;
  }, []);

  const focusAnchor = useMemo(() => {
    if (personFocus) {
      return points.find((p) => p.id === personFocus) ?? null;
    }
    if (focusId?.startsWith("o:")) {
      const inst = points.filter((p) => p.institutionId === focusId);
      if (inst.length === 0) return null;
      return {
        x: inst.reduce((s, p) => s + p.x, 0) / inst.length,
        y: inst.reduce((s, p) => s + p.y, 0) / inst.length,
      };
    }
    return null;
  }, [personFocus, focusId, points]);

  const nearRadius2 = NEAR_RADIUS * NEAR_RADIUS;
  const isFar = useCallback(
    (p: ProjectionPoint) => {
      if (!focusAnchor) return false;
      if (p.id === focusId) return false;
      return dist2(p, focusAnchor) > nearRadius2;
    },
    [focusAnchor, focusId, nearRadius2],
  );

  const isEdgeNear = useCallback(
    (edge: ProjectionEdge) => {
      if (!focusId) return true;
      return edge.sourceId === focusId || edge.targetId === focusId;
    },
    [focusId],
  );

  const worldTransform = `translate(${transform.tx} ${transform.ty}) scale(${transform.scale} ${-transform.scale})`;
  const invScale = 1 / transform.scale;

  return (
    <div
      className={`people-scatter ${className ?? ""}`.trim()}
      style={{ minHeight: minHeight ?? 560 }}
    >
      <svg
        ref={svgRef}
        className="people-scatter__svg"
        viewBox={`0 0 ${size.w} ${size.h}`}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={endDrag}
      >
        <g transform={worldTransform}>
          <g className="people-scatter__edges" aria-hidden="true">
            {edges.map((edge) => {
              const a = pointById.get(edge.sourceId);
              const b = pointById.get(edge.targetId);
              if (!a || !b) return null;
              const near = isEdgeNear(edge);
              const far =
                focusAnchor != null &&
                dist2(a, focusAnchor) > nearRadius2 &&
                dist2(b, focusAnchor) > nearRadius2;
              return (
                <line
                  key={edgeKey(edge)}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={near && focusId ? FOCUS_EDGE_COLOR : EDGE_COLOR}
                  strokeWidth={
                    (near && focusId ? 1 : 0.55 + Math.min(edge.weight, 8) * 0.03) *
                    invScale
                  }
                  strokeOpacity={far ? 0.15 : near && focusId ? 0.9 : 0.45}
                  className="people-scatter__edge"
                />
              );
            })}
          </g>

          {points.map((p) => {
            const isFocus = p.id === focusId;
            const far = isFar(p);
            const isHover = hovered?.id === p.id;
            const baseR = isFocus
              ? Math.max(FOCUS_R, radiusForImpact(p.impact) + 0.8)
              : radiusForImpact(p.impact);
            const r = (baseR + (isHover ? 0.8 : 0)) * invScale;
            return (
              <circle
                key={p.id}
                data-scatter-point
                cx={p.x}
                cy={p.y}
                r={r}
                fill={
                  isFocus
                    ? FOCUS_COLOR
                    : far
                      ? FAR_POINT_COLOR
                      : colourForInstitution(p.institutionId)
                }
                stroke={
                  isHover || isFocus
                    ? "#ffffff"
                    : "rgba(255, 255, 255, 0.65)"
                }
                strokeWidth={(isHover ? 1.5 : isFocus ? 1.25 : 0.75) * invScale}
                opacity={far ? 0.35 : isFocus ? 1 : 0.82}
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() =>
                  setHovered((h) => (h?.id === p.id ? null : h))
                }
                onClick={(e) => {
                  e.stopPropagation();
                  onFocus(p.id);
                }}
                className="people-scatter__point"
              />
            );
          })}
        </g>
      </svg>

      {hovered && (
        <div className="people-scatter__tooltip" role="status">
          <strong>{hovered.label}</strong>
          <span>
            {hovered.institution ?? "—"}
            {hovered.rank ? ` · ${humanRank(hovered.rank)}` : ""}
            {(degreeById.get(hovered.id) ?? 0) > 0
              ? ` · ${degreeById.get(hovered.id)} links`
              : ""}
          </span>
        </div>
      )}

      <div className="people-scatter__status">
        {loading && (import.meta.env.VITE_API_URL ? "connecting to API…" : "loading graph…")}
        {error && (
          <span className="people-scatter__error">
            {"projection failed: " + error.message}
          </span>
        )}
        {!loading && !error && (
          <span>
            {points.length} people · {edges.length} links ·{" "}
            {data?.projection.algorithm ?? ""}
          </span>
        )}
      </div>
    </div>
  );
}

function humanRank(rank: string): string {
  return rank.replace(/_/g, " ");
}
