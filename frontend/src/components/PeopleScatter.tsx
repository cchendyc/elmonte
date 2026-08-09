/**
 * PeopleScatter — El Monte people map.
 *
 * Each point is a researcher projected by structural similarity. Closer points
 * are more related; size reflects impact. Color mode is user-selectable.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@apollo/client/react";
import type { ProjectionData, ProjectionPoint } from "../api/projection";
import { PROJECTION } from "../api/projection";
import type { PersonCoauthorTiesData } from "../api/coauthorTies";
import { PERSON_COAUTHOR_TIES } from "../api/coauthorTies";
import { PERSPECTIVE, type PerspectiveData } from "../api/perspective";
import { groupThetas, importanceToRadius, polarToCartesian } from "../lib/perspectiveLayout";
import {
  buildCategoryColorMaps,
  buildLegend,
  clusterColorSlot,
  FOCUS_COLOR,
  FOCUS_RING_COLOR,
  pointFill,
  pointOpacity,
  SCATTER_COLOR_MODES,
  type ScatterColorMode,
} from "../lib/scatterColor";
import {
  clusterHulls,
  curvedEdgePath,
  densestClusterCentroid,
  edgeWidthScale,
  minPairDistSq,
} from "../lib/scatterLayout";

interface Props {
  focusId: string | null;
  onFocus: (id: string) => void;
  minHeight?: number | string;
  className?: string;
}

interface Transform {
  tx: number;
  ty: number;
  scale: number;
}

type AtlasView = "topic" | "network";
type EdgeType = "collaboration" | "topic";

const VIEW_STORAGE_KEY = "elmonte-scatter-view";
const EDGE_STORAGE_KEY = "elmonte-scatter-edge-type";
const DEFAULT_EDGE_COUNT = 30;

// --- shareable view state ---------------------------------------------------
// map/color/since/edges are mirrored into URL search params so a shared link
// reproduces the sender's view (localStorage stays the fallback default).

function urlParam(name: string): string | null {
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}

function syncUrlParam(name: string, value: string | null): void {
  try {
    const url = new URL(window.location.href);
    if (value === null || value === "") {
      url.searchParams.delete(name);
    } else {
      url.searchParams.set(name, value);
    }
    window.history.replaceState(null, "", url.toString());
  } catch {
    // ignore
  }
}

function loadView(): AtlasView {
  const param = urlParam("map");
  if (param === "network" || param === "topic") return param;
  try {
    const raw = localStorage.getItem(VIEW_STORAGE_KEY);
    if (raw === "network" || raw === "topic") return raw;
  } catch {
    // ignore
  }
  return "topic";
}

function loadEdgeType(): EdgeType {
  const param = urlParam("edges");
  if (param === "collaboration" || param === "topic") return param;
  try {
    return localStorage.getItem(EDGE_STORAGE_KEY) === "collaboration"
      ? "collaboration"
      : "topic";
  } catch {
    return "topic";
  }
}

const IDENTITY: Transform = { tx: 0, ty: 0, scale: 1 };
const MIN_SCREEN_R = 2.8;
const MAX_SCREEN_R = 9;
const FOCUS_SIZE_BOOST = 2.5;
const PAD = 24;
const WHEEL_STEP = 0.008;
const ZOOM_MIN_FACTOR = 0.2;
const ZOOM_MAX_FACTOR = 24;
const FOCUS_WORLD_RADIUS = 0.55;
const FOCUS_ZOOM_FACTOR = 2.2;
const LABEL_ZOOM_FACTOR = 3.0;
const MIN_LABEL_SEPARATION_SCREEN_PX = 34;
const COLOR_MODE_STORAGE_KEY = "elmonte-scatter-color-mode";
const SHOW_NAMES_STORAGE_KEY = "elmonte-scatter-show-names";
const SINCE_YEAR_STORAGE_KEY = "elmonte-scatter-since-year";

const TIE_COLOR = "#4338ca";
const TIE_COLOR_STRONG = "#1d4ed8";
const OUTSIDE_NETWORK_OPACITY = 0.42;

// Initial-view separation guarantee: with few people, dense clusters must not
// render as an unreadable blob — the camera zooms until the closest pair is
// at least this far apart on screen (bounded, so pathological data can't
// explode the scale).
const MIN_SEP_SCREEN_PX = 46;
const MIN_SEP_MAX_POINTS = 250;
const MIN_SEP_MAX_ZOOM = 3;

// Maps with this many people or fewer show every label by default — a dozen
// anonymous dots is an empty-looking map, not a visualization.
const LABEL_ALL_POINTS = 24;

function radiusForImpactScreen(impact: number, n: number): number {
  // NaN-safe: a degenerate impact must not poison the radius math.
  const v = Number.isFinite(impact) ? impact : 0;
  // Sparse maps get proportionally bigger points so a handful of people
  // reads as a map instead of dust; converges to the base scale as n grows.
  const boost = Math.max(1, Math.min(2.0, Math.pow(40 / Math.max(n, 5), 0.55)));
  const t = Math.pow(Math.max(0, Math.min(1, v)), 0.65);
  return Math.min(
    MAX_SCREEN_R + 5,
    (MIN_SCREEN_R + t * (MAX_SCREEN_R - MIN_SCREEN_R)) * boost,
  );
}

function impactLabel(impact: number): string {
  if (impact <= 0.02) return "no publications recorded";
  if (impact >= 0.85) return "high impact";
  if (impact >= 0.55) return "moderate impact";
  if (impact >= 0.25) return "emerging";
  return "early career";
}

interface CoauthorLine {
  personId: string;
  paperCount: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

function tieStrokeScreenWidth(
  paperCount: number,
  min: number,
  max: number,
): number {
  if (max <= min) return 4.5;
  const t = (paperCount - min) / (max - min);
  return 2.8 + t * 5.2;
}

function tieOpacity(paperCount: number, min: number, max: number): number {
  if (max <= min) return 0.72;
  const t = (paperCount - min) / (max - min);
  return 0.42 + t * 0.5;
}

function dist2(
  a: { x: number; y: number },
  b: { x: number; y: number },
): number {
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return dx * dx + dy * dy;
}

function clampScale(scale: number, overviewScale: number): number {
  const minScale = overviewScale * ZOOM_MIN_FACTOR;
  const maxScale = overviewScale * ZOOM_MAX_FACTOR;
  return Math.max(minScale, Math.min(maxScale, scale));
}

function bboxOf(points: ProjectionPoint[]) {
  if (points.length === 0) {
    return { xMin: -1, yMin: -1, xMax: 1, yMax: 1 };
  }
  let xMin = Infinity;
  let yMin = Infinity;
  let xMax = -Infinity;
  let yMax = -Infinity;
  for (const p of points) {
    // Defense in depth: points are sanitized upstream, but a single NaN here
    // would turn the whole fit transform into NaN and blank the map.
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
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
  let cx = (xMin + xMax) / 2;
  let cy = (yMin + yMax) / 2;

  // Sparse/medium maps: guarantee a minimum screen separation so tight
  // clusters are legible at the initial view, and center the camera on the
  // densest cluster when that zoom exceeds the bounding-box fit — otherwise
  // the interesting region sits off-screen and the user must pan+zoom
  // manually (the exact complaint this fixes).
  if (points.length >= 2 && points.length <= MIN_SEP_MAX_POINTS) {
    const minD = Math.sqrt(minPairDistSq(points));
    if (minD > 1e-9) {
      const sepScale = Math.min(
        MIN_SEP_SCREEN_PX / minD,
        scale * MIN_SEP_MAX_ZOOM,
      );
      if (sepScale > scale) {
        const densest = densestClusterCentroid(points);
        if (densest) {
          cx = densest.x;
          cy = densest.y;
        }
        return {
          tx: size.w / 2 - sepScale * cx,
          ty: size.h / 2 + sepScale * cy,
          scale: sepScale,
        };
      }
    }
  }

  return {
    tx: size.w / 2 - scale * cx,
    ty: size.h / 2 + scale * cy,
    scale,
  };
}

function fitMembersTransform(
  members: ProjectionPoint[],
  size: { w: number; h: number },
  minScale: number,
): Transform {
  const t = fitAllTransform(members, size);
  return { ...t, scale: Math.max(t.scale, minScale) };
}

function panToPointTransform(
  point: ProjectionPoint,
  size: { w: number; h: number },
  current: Transform,
  overviewScale: number,
): Transform {
  const focusFloor = overviewScale * FOCUS_ZOOM_FACTOR;
  const scale = Math.max(current.scale, focusFloor);
  return {
    tx: size.w / 2 - scale * point.x,
    ty: size.h / 2 + scale * point.y,
    scale,
  };
}

function loadShowNames(): boolean {
  // Personal-network labels default ON — clicking a person with anonymous
  // dots and no edge marks was the top user complaint.  Only an explicit
  // "0" (user unchecked the box) keeps them off.
  try {
    return localStorage.getItem(SHOW_NAMES_STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}

function shortLabel(label: string, max = 22): string {
  if (label.length <= max) return label;
  return `${label.slice(0, max - 1)}…`;
}

function loadColorMode(): ScatterColorMode {
  const param = urlParam("color");
  if (param === "cluster" || param === "institution" || param === "focus") {
    return param;
  }
  try {
    const raw = localStorage.getItem(COLOR_MODE_STORAGE_KEY);
    if (raw === "cluster" || raw === "institution" || raw === "focus") {
      return raw;
    }
    if (raw === "similarity" || raw === "community") return "cluster";
  } catch {
    // ignore
  }
  return "cluster";
}

function loadHiddenClusters(): Set<number> {
  try {
    const raw = localStorage.getItem("elmonte-hidden-clusters");
    if (!raw) return new Set();
    return new Set(raw.split(",").map(Number).filter((n) => !isNaN(n)));
  } catch {
    return new Set();
  }
}

function persistHiddenClusters(set: Set<number>): void {
  try {
    localStorage.setItem("elmonte-hidden-clusters", [...set].join(","));
  } catch {
    // ignore
  }
}

function loadSinceYear(): number | null {
  const param = urlParam("since");
  if (param !== null) {
    const n = parseInt(param, 10);
    if (!isNaN(n)) return n;
  }
  try {
    const raw = localStorage.getItem(SINCE_YEAR_STORAGE_KEY);
    if (!raw) return null;
    const n = parseInt(raw, 10);
    return !isNaN(n) ? n : null;
  } catch {
    return null;
  }
}

// F4: viewport culling helpers
interface WorldRect {
  xMin: number;
  yMin: number;
  xMax: number;
  yMax: number;
}

function worldRectFromTransform(t: Transform, size: { w: number; h: number }): WorldRect {
  const s = t.scale;
  const margin = 1.5;
  const hw = (size.w / s) * margin / 2;
  const hh = (size.h / s) * margin / 2;
  const cx = (size.w / 2 - t.tx) / s;
  const cy = (t.ty - size.h / 2) / s;
  return {
    xMin: cx - hw,
    xMax: cx + hw,
    yMin: cy - hh,
    yMax: cy + hh,
  };
}

function inRect(p: { x: number; y: number }, rect: WorldRect): boolean {
  return p.x >= rect.xMin && p.x <= rect.xMax && p.y >= rect.yMin && p.y <= rect.yMax;
}

export function PeopleScatter({ focusId, onFocus, minHeight, className }: Props) {
  const [view, setView] = useState<AtlasView>(loadView);
  const [edgeType, setEdgeType] = useState<EdgeType>(loadEdgeType);

  const { data, loading, error } = useQuery<ProjectionData>(PROJECTION, {
    variables: { view },
    fetchPolicy: "cache-first",
  });
  // Flicker fix: when a view is refetched, Apollo briefly returns
  // `data: undefined` — fall back to the last map for the SAME view instead
  // of flashing blank (or, worse, flashing the OTHER view's map, which
  // reads as a stale flash rather than a transition).
  const projPrevRef = useRef<{ view: AtlasView; projection: ProjectionData["projection"] } | null>(null);
  if (data?.projection) {
    projPrevRef.current = { view, projection: data.projection };
  }
  const projection =
    data?.projection ??
    (projPrevRef.current?.view === view ? projPrevRef.current.projection : undefined);
  const rawPoints = projection?.points ?? [];
  // H1: drop degenerate points at the data boundary — NaN coordinates or
  // impact would poison bboxOf, hulls, and radius math downstream.
  const points = useMemo(
    () =>
      rawPoints.filter(
        (p) =>
          Number.isFinite(p.x) &&
          Number.isFinite(p.y) &&
          Number.isFinite(p.impact ?? 0),
      ),
    [rawPoints],
  );
  const clusters = projection?.clusters ?? [];
  const edges = projection?.edges ?? [];
  const runId = projection?.runId;

  const personFocus =
    focusId?.startsWith("p:") === true ? focusId : null;

  const { data: tieData, loading: tiesLoading, error: tiesError, previousData: tiePrev } = useQuery<PersonCoauthorTiesData>(PERSON_COAUTHOR_TIES, {
    variables: { personId: personFocus ?? "", view },
    skip: personFocus == null,
    fetchPolicy: "cache-first",
  });
  // Stale-data guard: Apollo's previousData can belong to the PREVIOUSLY
  // focused person — falling back to it during a focus switch would highlight
  // the wrong network (wrong dimming, wrong edge labels, wrong counts).
  const tiesFocusRef = useRef<string | null>(null);
  if (tieData) {
    tiesFocusRef.current = personFocus;
  }
  const coauthorTies =
    tieData?.personCoauthorTies ??
    (tiesFocusRef.current === personFocus ? tiePrev?.personCoauthorTies ?? [] : []);

  const [perspectiveMode, setPerspectiveMode] = useState(false);
  const { data: perspData, error: perspError, previousData: perspPrev } = useQuery<PerspectiveData>(PERSPECTIVE, {
    variables: { personId: personFocus ?? "" },
    skip: personFocus == null || !perspectiveMode,
    fetchPolicy: "cache-first",
  });
  const perspFocusRef = useRef<string | null>(null);
  if (perspData) {
    perspFocusRef.current = personFocus;
  }
  const alters =
    perspData?.perspective.alters ??
    (perspFocusRef.current === personFocus ? perspPrev?.perspective.alters ?? [] : []);

  const [colorMode, setColorMode] = useState<ScatterColorMode>(loadColorMode);
  const [showNames, setShowNames] = useState(loadShowNames);
  const [hiddenClusters, setHiddenClusters] = useState<Set<number>>(loadHiddenClusters);
  const [shareState, setShareState] = useState<"idle" | "copied">("idle");

  // F1: pulse animation state
  const [pulseKey, setPulseKey] = useState<string | null>(null);

  // F2: year filter state
  const [sinceYear, setSinceYear] = useState<number | null>(loadSinceYear);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
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

  const cameraTargetKey = runId
    ? personFocus
      ? `focus:${personFocus}:${size.w}x${size.h}`
      : `fit:${runId}:${size.w}x${size.h}`
    : null;

  const colorMaps = useMemo(
    () => buildCategoryColorMaps(points, clusters),
    [points, clusters],
  );

  const legend = useMemo(
    () => buildLegend(points, colorMode, colorMaps),
    [points, colorMode, colorMaps],
  );

  const onColorModeChange = useCallback((mode: ScatterColorMode) => {
    setColorMode(mode);
    syncUrlParam("color", mode);
    try {
      localStorage.setItem(COLOR_MODE_STORAGE_KEY, mode);
    } catch {
      // ignore
    }
  }, []);

  const onShowNamesChange = useCallback((enabled: boolean) => {
    setShowNames(enabled);
    try {
      localStorage.setItem(SHOW_NAMES_STORAGE_KEY, enabled ? "1" : "0");
    } catch {
      // ignore
    }
  }, []);

  const handleShare = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShareState("copied");
      setTimeout(() => setShareState("idle"), 1500);
    } catch {
      // clipboard unavailable — no-op
    }
  }, []);

  const handleExportPNG = useCallback(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const rect = svg.getBoundingClientRect();
    const w = rect.width || size.w;
    const h = rect.height || size.h;

    // Deep-clone the SVG so we don't mutate the visible one
    const clone = svg.cloneNode(true) as SVGSVGElement;
    // The clone duplicates the live DOM's ids (gradients, clip-paths,
    // aria-labelledby) — strip them so the document stays valid while the
    // clone is attached during style resolution.
    clone.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    clone.removeAttribute("id");
    clone.setAttribute("width", String(w));
    clone.setAttribute("height", String(h));

    // Temporarily attach clone to DOM so getComputedStyle resolves CSS classes.
    // Hidden off-screen to avoid layout thrash.
    clone.style.position = "absolute";
    clone.style.left = "-99999px";
    clone.style.top = "-99999px";
    document.body.appendChild(clone);
    try {
      // Inline computed styles on text elements (fill/stroke attrs are already inline
      // for points, hulls, and rects — only text uses CSS classes for color).
      const walk = (el: Element) => {
        if (el.tagName === "text" || el.tagName === "tspan") {
          const cs = window.getComputedStyle(el as Element as HTMLElement);
          el.setAttribute("fill", cs.fill);
          el.setAttribute("font-family", cs.fontFamily);
          el.setAttribute("font-size", cs.fontSize);
          el.setAttribute("font-weight", cs.fontWeight);
        }
        for (let i = 0; i < el.children.length; i++) walk(el.children[i]);
      };
      walk(clone);
    } finally {
      clone.remove();
    }

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);
    const blob = new Blob([svgString], {
      type: "image/svg+xml;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);

    const img = new Image();
    img.onload = () => {
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = w * scale;
      canvas.height = h * scale;
      const ctx = canvas.getContext("2d")!;
      ctx.scale(scale, scale);
      // White background so transparent areas render cleanly
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);

      canvas.toBlob((pngBlob) => {
        if (!pngBlob) return;
        const downloadUrl = URL.createObjectURL(pngBlob);
        const a = document.createElement("a");
        a.href = downloadUrl;
        a.download = "elmonte-atlas.png";
        a.click();
        URL.revokeObjectURL(downloadUrl);
      }, "image/png");
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }, [size.w, size.h]);

  const clampedScale = useCallback(
    (scale: number) => clampScale(scale, overviewScale.current),
    [],
  );

  const zoomAtPoint = useCallback(
    (px: number, py: number, factor: number) => {
      userPanned.current = true;
      setTransform((t) => {
        const scale = clampedScale(t.scale * factor);
        const f = scale / t.scale;
        return {
          tx: px - (px - t.tx) * f,
          ty: py - (py - t.ty) * f,
          scale,
        };
      });
    },
    [clampedScale],
  );

  const fitView = useCallback(() => {
    userPanned.current = false;
    cameraKey.current = null;
    if (points.length === 0) return;
    if (personFocus) {
      const point = points.find((p) => p.id === personFocus);
      if (point) {
        // Use the CURRENT transform as the base — IDENTITY here would reset
        // the user's zoom/pan on every "Fit" press.
        setTransform((t) => panToPointTransform(point, size, t, overviewScale.current));
        return;
      }
    }
    setTransform(fitAllTransform(points, size));
  }, [personFocus, points, size]);

  // F1: trigger pulse animation when personFocus changes
  useEffect(() => {
    if (personFocus) {
      setPulseKey(`${personFocus}-${Date.now()}`);
    } else {
      setPulseKey(null);
    }
  }, [personFocus]);

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

  useEffect(() => {
    if (!cameraTargetKey || points.length === 0) return;
    if (userPanned.current) return;
    if (cameraKey.current === cameraTargetKey) return;

    if (personFocus) {
      const point = points.find((p) => p.id === personFocus);
      if (!point) return;
      setTransform((t) =>
        panToPointTransform(point, size, t, overviewScale.current),
      );
    } else {
      setTransform(fitAllTransform(points, size));
    }
    cameraKey.current = cameraTargetKey;
  }, [cameraTargetKey, personFocus, points, size]);

  useEffect(() => {
    userPanned.current = false;
    cameraKey.current = null;
  }, [runId]);

  useEffect(() => {
    if (points.length === 0) return;
    overviewScale.current = fitAllTransform(points, size).scale;
  }, [points, size]);

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
      const step = e.ctrlKey ? WHEEL_STEP * 1.8 : WHEEL_STEP;

      setTransform((t) => {
        const raw = t.scale * Math.exp(-delta * step);
        const scale = clampedScale(raw);
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
  }, [size.w, size.h, clampedScale]);

  const transformRef = useRef(transform);
  transformRef.current = transform;

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

  const onDoubleClick = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if ((e.target as Element).closest("[data-scatter-point]")) return;
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * size.w;
      const py = ((e.clientY - rect.top) / rect.height) * size.h;
      zoomAtPoint(px, py, 1.65);
    },
    [size.w, size.h, zoomAtPoint],
  );

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

  // D3: per-person focus radius — larger networks get a wider near neighborhood
  const nearRadius2 = useMemo(() => {
    const focusDegree = coauthorTies.length;
    const r = Math.max(0.25, Math.min(0.8, FOCUS_WORLD_RADIUS * (1 + focusDegree / 40)));
    return r * r;
  }, [coauthorTies.length]);
  const isFar = useCallback(
    (p: ProjectionPoint) => {
      if (colorMode !== "focus" || !focusAnchor) return false;
      if (p.id === focusId) return false;
      return dist2(p, focusAnchor) > nearRadius2;
    },
    [colorMode, focusAnchor, focusId, nearRadius2],
  );

  const personalGraph = personFocus != null;
  const personalGraphActive = personalGraph && !tiesLoading;

  const coauthorIds = useMemo(() => {
    const ids = new Set(coauthorTies.map((t) => t.personId));
    if (personFocus) ids.add(personFocus);
    return ids;
  }, [coauthorTies, personFocus]);

  const coauthorPaperCount = useMemo(() => {
    const map = new Map<string, number>();
    for (const tie of coauthorTies) {
      map.set(tie.personId, tie.paperCount);
    }
    return map;
  }, [coauthorTies]);

  const isNetworkMember = useCallback(
    (id: string) => coauthorIds.has(id),
    [coauthorIds],
  );

  // F2: year filter — visiblePoints for hulls and member counts
  const yearOptions = useMemo(() => {
    const years = points
      .map((p) => p.lastPublicationYear)
      .filter((y): y is number => y != null);
    if (years.length === 0) return [];
    const min = Math.min(...years);
    const max = Math.max(...years);
    const buckets: number[] = [];
    for (let y = min; y <= max; y++) buckets.push(y);
    return buckets;
  }, [points]);

  const visiblePoints = useMemo(() => {
    if (sinceYear == null) return points;
    return points.filter((p) => (p.lastPublicationYear ?? 9999) >= sinceYear);
  }, [points, sinceYear]);

  const clusterMemberCounts = useMemo(() => {
    const map = new Map<number, number>();
    for (const p of visiblePoints) {
      if (p.clusterId != null) {
        map.set(p.clusterId, (map.get(p.clusterId) ?? 0) + 1);
      }
    }
    return map;
  }, [visiblePoints]);

  // M5/M6/M7: group cluster members once — from the same (year-filtered) set
  // used for hulls — so label pills, hull polygons, and zoom-to-cluster all
  // agree under the year filter, and no per-frame O(n*m) filter runs.
  const pointsByCluster = useMemo(() => {
    const map = new Map<number, ProjectionPoint[]>();
    for (const p of visiblePoints) {
      if (p.clusterId == null) continue;
      const arr = map.get(p.clusterId);
      if (arr) arr.push(p);
      else map.set(p.clusterId, [p]);
    }
    return map;
  }, [visiblePoints]);

  const hullsByCluster = useMemo(
    () => clusterHulls(visiblePoints, clusters),
    [visiblePoints, clusters],
  );

  // Z-order: network members on top, then non-far, then focus last (on top).
  // Deps note: the body reads personalGraphActive (ties loaded) AND
  // isNetworkMember — both must stay in the deps or the sort silently stops
  // re-running when coauthor data arrives.
  const sortedPoints = useMemo(() => {
    return [...points].sort((a, b) => {
      if (personalGraphActive) {
        const aNet = isNetworkMember(a.id) ? 1 : 0;
        const bNet = isNetworkMember(b.id) ? 1 : 0;
        if (aNet !== bNet) return aNet - bNet;
      }
      const aFar = isFar(a) ? 0 : 1;
      const bFar = isFar(b) ? 0 : 1;
      if (aFar !== bFar) return aFar - bFar;
      const aFocus = personFocus != null && a.id === personFocus ? 1 : 0;
      const bFocus = personFocus != null && b.id === personFocus ? 1 : 0;
      return aFocus - bFocus;
    });
  }, [points, isFar, personFocus, personalGraph, personalGraphActive, isNetworkMember]);

  // F4: viewport culling — filter sortedPoints by visible rect
  const visibleRect = useMemo(
    () => worldRectFromTransform(transform, size),
    [transform, size],
  );

  const renderPoints = useMemo(() => {
    return sortedPoints.filter(
      (p) =>
        inRect(p, visibleRect) ||
        p.id === hovered?.id ||
        p.id === personFocus,
    );
  }, [sortedPoints, visibleRect, hovered?.id, personFocus]);

  // N2: leader line from hovered point to tooltip (world-space coords)
  // Uses state + useLayoutEffect (not useMemo) so the tooltip ref is
  // attached by the commit phase before we measure getBoundingClientRect.
  const [leaderLine, setLeaderLine] = useState<{
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  } | null>(null);

  useLayoutEffect(() => {
    if (!hovered || personalGraph) {
      setLeaderLine(null);
      return;
    }
    const tooltipEl = tooltipRef.current;
    const svgEl = svgRef.current;
    if (!tooltipEl || !svgEl) return;
    const svgRect = svgEl.getBoundingClientRect();
    const tipRect = tooltipEl.getBoundingClientRect();
    if (tipRect.width === 0 || tipRect.height === 0) return;
    // Anchor at the bottom-center of the tooltip
    const anchorX = tipRect.left + tipRect.width / 2;
    const anchorY = tipRect.bottom;
    // Relative to SVG element
    const relX = anchorX - svgRect.left;
    const relY = anchorY - svgRect.top;
    // Convert to viewBox coordinates (respecting preserveAspectRatio xMidYMid meet)
    const fitScale = Math.min(svgRect.width / size.w, svgRect.height / size.h);
    const renderedW = size.w * fitScale;
    const renderedH = size.h * fitScale;
    const padX = (svgRect.width - renderedW) / 2;
    const padY = (svgRect.height - renderedH) / 2;
    const viewX = (relX - padX) / fitScale;
    const viewY = (relY - padY) / fitScale;
    // Convert to world coordinates (inverse of translate(tx, ty) scale(scale, -scale))
    const { tx, ty, scale } = transform;
    setLeaderLine({
      x1: hovered.x,
      y1: hovered.y,
      x2: (viewX - tx) / scale,
      y2: (ty - viewY) / scale,
    });
  }, [hovered, personalGraph, transform, size]);

  const pointById = useMemo(() => {
    const map = new Map<string, ProjectionPoint>();
    for (const p of points) map.set(p.id, p);
    return map;
  }, [points]);

  const coauthorLines = useMemo((): CoauthorLine[] => {
    if (!personFocus) return [];
    const focusPoint = pointById.get(personFocus);
    if (!focusPoint) return [];
    const lines: CoauthorLine[] = [];
    for (const tie of coauthorTies) {
      const other = pointById.get(tie.personId);
      if (!other) continue;
      lines.push({
        personId: tie.personId,
        paperCount: tie.paperCount,
        x1: focusPoint.x,
        y1: focusPoint.y,
        x2: other.x,
        y2: other.y,
      });
    }
    return lines.sort((a, b) => a.paperCount - b.paperCount);
  }, [personFocus, coauthorTies, pointById]);

  const tiePaperRange = useMemo(() => {
    if (coauthorLines.length === 0) {
      return { min: 0, max: 0 };
    }
    let min = coauthorLines[0].paperCount;
    let max = coauthorLines[0].paperCount;
    for (const line of coauthorLines) {
      if (line.paperCount < min) min = line.paperCount;
      if (line.paperCount > max) max = line.paperCount;
    }
    return { min, max };
  }, [coauthorLines]);

  const edgeList = useMemo(() => {
    const e = edges
      .filter((ed) => ed.sourceCluster !== ed.targetCluster)
      .map((ed) => ({
        ...ed,
        weight:
          edgeType === "collaboration"
            ? ed.collaborationWeight ?? 0
            : ed.topicWeight ?? 0,
      }))
      .filter((ed) => ed.weight > 0)
      .sort((a, b) => b.weight - a.weight)
      .slice(0, DEFAULT_EDGE_COUNT);
    const maxW = e.length ? e[0].weight : 0;
    return { list: e, maxW };
  }, [edges, edgeType]);

  const clusterById = useMemo(
    () => new Map(clusters.map((c) => [c.id, c])),
    [clusters],
  );

  const worldTransform = `translate(${transform.tx} ${transform.ty}) scale(${transform.scale} ${-transform.scale})`;
  const invScale = 1 / transform.scale;

  return (
    <div
      className={`people-scatter ${className ?? ""}`.trim()}
      style={{ minHeight: minHeight ?? 560 }}
    >
      <div className="people-scatter__header">
        <div className="people-scatter__header-main">
          <span className="people-scatter__title">
            {personalGraph ? "Personal network" : "People map"}
          </span>
          <div className="people-scatter__header-controls">
            {personalGraph && (
              <label className="people-scatter__show-names">
                <input
                  type="checkbox"
                  checked={showNames}
                  onChange={(e) => onShowNamesChange(e.target.checked)}
                />
                <span>Show names</span>
              </label>
            )}
            {personalGraph && (
              <button
                type="button"
                className={`people-scatter__perspective${perspectiveMode ? " is-active" : ""}`}
                onClick={() => setPerspectiveMode((v) => !v)}
              >
                {perspectiveMode ? "Exit perspective" : "Perspective"}
              </button>
            )}
            {/* F2: year filter — visible in BOTH modes so an active filter
                can never become invisible state (it still dims points). */}
            {yearOptions.length > 0 && (
              <label className="people-scatter__year-filter">
                <span>Since</span>
                <select
                  value={sinceYear ?? ""}
                  onChange={(e) => {
                    const v = e.target.value;
                    const y = v ? parseInt(v, 10) : null;
                    setSinceYear(y);
                    syncUrlParam("since", v || null);
                    try {
                      localStorage.setItem(SINCE_YEAR_STORAGE_KEY, v);
                    } catch {
                      // ignore
                    }
                  }}
                  aria-label="Filter by last publication year"
                >
                  <option value="">All years</option>
                  {yearOptions.map((y) => (
                    <option key={y} value={y}>
                      {y}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!personalGraph && (
              <>
                <label className="people-scatter__view">
                  <span>View</span>
                  <select
                    value={view}
                    onChange={(e) => {
                      const v = e.target.value as AtlasView;
                      setView(v);
                      syncUrlParam("map", v);
                      try {
                        localStorage.setItem(VIEW_STORAGE_KEY, v);
                      } catch {
                        // ignore
                      }
                    }}
                    aria-label="Map view"
                  >
                    <option value="topic">Topic</option>
                    <option value="network">Network</option>
                  </select>
                </label>
                <label className="people-scatter__edge-type">
                  <span>Edges</span>
                  <select
                    value={edgeType}
                    onChange={(e) => {
                      const v = e.target.value as EdgeType;
                      setEdgeType(v);
                      syncUrlParam("edges", v);
                      try {
                        localStorage.setItem(EDGE_STORAGE_KEY, v);
                      } catch {
                        // ignore
                      }
                    }}
                    aria-label="Cluster edge type"
                  >
                    <option value="collaboration">Collaboration</option>
                    <option value="topic">Topic</option>
                  </select>
                </label>
                <label className="people-scatter__color-by">
                  <span>Color by</span>
                  <select
                    value={colorMode}
                    onChange={(e) =>
                      onColorModeChange(e.target.value as ScatterColorMode)
                    }
                    aria-label="Color points by"
                  >
                    {SCATTER_COLOR_MODES.map((mode) => (
                      <option key={mode.id} value={mode.id}>
                        {mode.label}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
          </div>
        </div>
        <span className="people-scatter__subtitle">
          {personalGraph ? (
            tiesError ? (
              "coauthor network failed to load"
            ) : tiesLoading && coauthorTies.length === 0 ? (
              "Loading network…"
            ) : (
              `${coauthorTies.length} coauthors on map · line width = shared papers`
            )
          ) : (
            `${points.length} researchers · ${view} view · ${edgeList.list.length} cluster edges`
          )}
        </span>
      </div>

      {legend.length > 0 && !personalGraph && (
        <ul className="people-scatter__legend" aria-label="Map legend">
          {legend.map((entry) => (
            <li key={entry.key}>
              <span
                className="people-scatter__legend-swatch"
                style={{ background: entry.color }}
                aria-hidden="true"
              />
              <span className="people-scatter__legend-label">{entry.label}</span>
              <span className="people-scatter__legend-count">{entry.count}</span>
            </li>
          ))}
        </ul>
      )}

      {/* D4: cluster legend with show/hide toggles */}
      {colorMode === "cluster" && !personalGraph && clusters.length > 0 && (
        <ul className="people-scatter__legend" aria-label="Cluster legend">
          {clusters.map((c) => {
            const hidden = hiddenClusters.has(c.id);
            return (
              <li
                key={c.id}
                style={{ opacity: hidden ? 0.35 : 1 }}
              >
                <span
                  className="people-scatter__legend-swatch"
                  style={{ background: clusterColorSlot(c.colorSlot) }}
                  aria-hidden="true"
                />
                <button
                  type="button"
                  className="people-scatter__legend-toggle"
                  onClick={() => {
                    setHiddenClusters((prev) => {
                      const next = new Set(prev);
                      if (next.has(c.id)) next.delete(c.id);
                      else next.add(c.id);
                      persistHiddenClusters(next);
                      return next;
                    });
                  }}
                  aria-label={`${hidden ? "Show" : "Hide"} cluster ${c.label}`}
                >
                  {c.label}
                </button>
                <span className="people-scatter__legend-count">
                  {clusterMemberCounts.get(c.id) ?? c.memberCount}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <div className="people-scatter__zoom" aria-label="Map zoom controls">
        <button
          type="button"
          className="people-scatter__zoom-btn"
          aria-label="Zoom in"
          onClick={() => zoomAtPoint(size.w / 2, size.h / 2, 1.4)}
        >
          +
        </button>
        <button
          type="button"
          className="people-scatter__zoom-btn"
          aria-label="Zoom out"
          onClick={() => zoomAtPoint(size.w / 2, size.h / 2, 1 / 1.4)}
        >
          −
        </button>
        <button
          type="button"
          className="people-scatter__zoom-btn people-scatter__zoom-btn--fit"
          aria-label="Fit map to view"
          onClick={fitView}
        >
          Fit
        </button>
        {/* D2: share + PNG export */}
        {!personalGraph && (
          <>
            <button
              type="button"
              className="people-scatter__zoom-btn"
              aria-label="Share map link"
              onClick={handleShare}
            >
              {shareState === "copied" ? "Copied" : "Share"}
            </button>
            <button
              type="button"
              className="people-scatter__zoom-btn"
              aria-label="Export map as PNG"
              onClick={handleExportPNG}
            >
              PNG
            </button>
          </>
        )}
      </div>

      {points.length === 0 && loading && (
        <div className="people-scatter__loading" role="status">
          Loading map…
        </div>
      )}

      <svg
        ref={svgRef}
        className="people-scatter__svg"
        viewBox={`0 0 ${size.w} ${size.h}`}
        preserveAspectRatio="xMidYMid meet"
        tabIndex={0}
        aria-label="People map, pan with arrow keys, zoom with + and -"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerLeave={endDrag}
        onDoubleClick={onDoubleClick}
        onKeyDown={(e) => {
          // D5: keyboard navigation — skip when focus is in a form element
          const tag = (e.target as HTMLElement).tagName;
          if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
          switch (e.key) {
            case "ArrowLeft":
              e.preventDefault();
              userPanned.current = true;
              setTransform((t) => ({ ...t, tx: t.tx - 40 }));
              break;
            case "ArrowRight":
              e.preventDefault();
              userPanned.current = true;
              setTransform((t) => ({ ...t, tx: t.tx + 40 }));
              break;
            case "ArrowUp":
              e.preventDefault();
              userPanned.current = true;
              setTransform((t) => ({ ...t, ty: t.ty - 40 }));
              break;
            case "ArrowDown":
              e.preventDefault();
              userPanned.current = true;
              setTransform((t) => ({ ...t, ty: t.ty + 40 }));
              break;
            case "+":
            case "=":
              e.preventDefault();
              zoomAtPoint(size.w / 2, size.h / 2, 1.4);
              break;
            case "-":
              e.preventDefault();
              zoomAtPoint(size.w / 2, size.h / 2, 1 / 1.4);
              break;
            case "f":
              e.preventDefault();
              fitView();
              break;
          }
        }}
      >
        {perspectiveMode && personFocus ? (
          <g transform={worldTransform}>
            {(() => {
              const cx = 0;
              const cy = 0;
              const radiusPx = Math.min(size.w, size.h) * 0.38;
              const thetas = groupThetas((i) => alters[i]?.group ?? 0, alters.length);
              const focusPoint = pointById.get(personFocus);
              const focusLabel = focusPoint?.label ?? "You";
              return (
                <>
                  <circle cx={cx} cy={cy} r={radiusPx} fill="none" stroke="#d4d4d8" strokeDasharray="4 4" />
                  {alters.map((alt, i) => {
                    const r = importanceToRadius(alt.importance);
                    const pt = polarToCartesian({ x: cx, y: cy }, { r, theta: thetas[i] }, radiusPx);
                    const size = 3 + Math.min(9, (alt.paperCount ?? 0) / Math.max(1, perspData?.perspective.maxPaperCount ?? 1) * 8);
                    return (
                      <g key={alt.personId}>
                        <line
                          x1={cx} y1={cy} x2={pt.x} y2={pt.y}
                          stroke={alt.hop === 2 ? "#a1a1aa" : "#6366f1"}
                          strokeWidth={0.5 * (1 + 4 * alt.importance)}
                          opacity={alt.hop === 2 ? 0.3 : 0.35 + 0.5 * alt.importance}
                          strokeLinecap="round"
                        />
                        <circle
                          cx={pt.x} cy={pt.y} r={size * invScale}
                          fill={clusterColorSlot(alt.group % 12)}
                          opacity={alt.hop === 2 ? 0.4 : 0.9}
                          data-scatter-point
                          onClick={(e) => {
                            e.stopPropagation();
                            onFocus(alt.personId);
                          }}
                        >
                          <title>{`${alt.label} — ${alt.paperCount ?? 0} papers together`}</title>
                        </circle>
                      </g>
                    );
                  })}
                  <circle cx={cx} cy={cy} r={9 * invScale} fill={FOCUS_COLOR} />
                  <text x={cx} y={cy - 14 * invScale} textAnchor="middle" className="people-scatter__perspective-focus">
                    {shortLabel(focusLabel, 20)}
                  </text>
                  {perspError && (
                    <text
                      x={cx}
                      y={cy + 26 * invScale}
                      textAnchor="middle"
                      className="people-scatter__perspective-focus"
                    >
                      perspective failed to load
                    </text>
                  )}
                </>
              );
            })()}
          </g>
        ) : (
        <g transform={worldTransform}>
          {personalGraphActive && coauthorLines.length > 0 && (
            <g className="people-scatter__ties" pointerEvents="none">
              {coauthorLines.map((line) => {
                const strokeW =
                  tieStrokeScreenWidth(
                    line.paperCount,
                    tiePaperRange.min,
                    tiePaperRange.max,
                  ) * invScale;
                const opacity = tieOpacity(
                  line.paperCount,
                  tiePaperRange.min,
                  tiePaperRange.max,
                );
                const t =
                  tiePaperRange.max > tiePaperRange.min
                    ? (line.paperCount - tiePaperRange.min) /
                      (tiePaperRange.max - tiePaperRange.min)
                    : 1;
                const strokeColor =
                  t > 0.55 ? TIE_COLOR_STRONG : TIE_COLOR;
                const mx = (line.x1 + line.x2) / 2;
                const my = (line.y1 + line.y2) / 2;
                return (
                  <g key={line.personId}>
                    <line
                      x1={line.x1}
                      y1={line.y1}
                      x2={line.x2}
                      y2={line.y2}
                      stroke="#ffffff"
                      strokeWidth={strokeW + 2.2 * invScale}
                      strokeLinecap="round"
                      opacity={0.85}
                    />
                    <line
                      x1={line.x1}
                      y1={line.y1}
                      x2={line.x2}
                      y2={line.y2}
                      className="people-scatter__tie"
                      stroke={strokeColor}
                      strokeWidth={strokeW}
                      strokeLinecap="round"
                      opacity={opacity}
                    />
                    <g
                      transform={`translate(${mx} ${my}) scale(${invScale} ${-invScale})`}
                    >
                      <text
                        className="people-scatter__tie-label"
                        textAnchor="middle"
                        dy={-5}
                      >
                        {line.paperCount}
                      </text>
                    </g>
                  </g>
                );
              })}
            </g>
          )}

          {/* Inter-cluster weighted edges (bezier curves) */}
          {!personalGraph && edgeList.list.length > 0 && (
            <g className="people-scatter__cluster-edges" pointerEvents="none">
              {edgeList.list.map((ed) => {
                // D4: skip edges connected to hidden clusters
                if (
                  hiddenClusters.has(ed.sourceCluster) ||
                  hiddenClusters.has(ed.targetCluster)
                )
                  return null;
                const a = clusterById.get(ed.sourceCluster);
                const b = clusterById.get(ed.targetCluster);
                if (!a || !b) return null;
                const t = edgeWidthScale(ed.weight, edgeList.maxW);
                const width = (1 + t * 6) * invScale;
                const opacity = 0.18 + t * 0.4;
                return (
                  <path
                    key={`${ed.sourceCluster}-${ed.targetCluster}`}
                    d={curvedEdgePath(
                      { x: a.cx, y: a.cy },
                      { x: b.cx, y: b.cy },
                      0.04,
                    )}
                    fill="none"
                    stroke={
                      edgeType === "collaboration" ? "#4338ca" : "#0f766e"
                    }
                    strokeWidth={width}
                    strokeLinecap="round"
                    opacity={opacity}
                  />
                );
              })}
            </g>
          )}

          {/* Cluster hulls — F2: use visiblePoints */}
          {!personalGraph && (
            <g className="people-scatter__clusters" pointerEvents="none">
              {[...hullsByCluster.entries()]
                .filter(([id]) => !hiddenClusters.has(id))
                .map(
                ([id, poly]) => {
                  const c = clusterById.get(id);
                  if (!c) return null;
                  const pts = poly
                    .map((p) => `${p.x},${p.y}`)
                    .join(" ");
                  return (
                    <polygon
                      key={id}
                      points={pts}
                      fill={clusterColorSlot(c.colorSlot)}
                      opacity={0.08}
                      stroke={clusterColorSlot(c.colorSlot)}
                      strokeWidth={1.2 * invScale}
                      strokeOpacity={0.5}
                    />
                  );
                },
              )}
            </g>
          )}

          {/* N2: leader line connecting hovered point to tooltip */}
          {leaderLine && (
            <line
              x1={leaderLine.x1}
              y1={leaderLine.y1}
              x2={leaderLine.x2}
              y2={leaderLine.y2}
              stroke="#a1a1aa"
              strokeDasharray="3 3"
              opacity={0.4}
              strokeWidth={1.2 * invScale}
              pointerEvents="none"
            />
          )}

          {/* F4: use renderPoints for viewport-culled rendering */}
          {/* N1: semantic zoom labels — always on for sparse maps, otherwise
              at close zoom; both decluttered so they never overlap */}
          {(() => {
            const labelAll = !personalGraph && points.length <= LABEL_ALL_POINTS;
            const semanticZoomActive =
              labelAll ||
              (!personalGraph &&
                transform.scale >
                  overviewScale.current * LABEL_ZOOM_FACTOR);
            const minWorldDist2 = semanticZoomActive
              ? (MIN_LABEL_SEPARATION_SCREEN_PX * invScale) ** 2
              : Infinity;
            let lastLabeledPos: { x: number; y: number } | null = null;

            return renderPoints.map((p) => {
            const isFocus = personFocus != null && p.id === personFocus;
            const inNetwork = isNetworkMember(p.id);
            const outsideNetwork = personalGraphActive && !inNetwork;
            const far = personalGraphActive ? false : isFar(p);
            const isHover = hovered?.id === p.id;
            const baseScreenR =
              radiusForImpactScreen(p.impact, points.length) + (isHover ? 1.2 : 0);
            const screenR = isFocus
              ? Math.min(baseScreenR + FOCUS_SIZE_BOOST, MAX_SCREEN_R + 2)
              : baseScreenR;
            const r = screenR * invScale;
            const cx = p.x;
            const cy = p.y;
            const fill = pointFill({
              point: p,
              mode: colorMode,
              focusId,
              isFar: far,
              isFocus,
              colors: colorMaps,
            });
            let opacity = pointOpacity({
              mode: colorMode,
              focusId,
              isFar: far,
              isFocus,
              isHover,
            });
            if (outsideNetwork) {
              opacity = Math.min(opacity, OUTSIDE_NETWORK_OPACITY);
            }
            // D4: dim points belonging to hidden clusters — map view only.
            // Personal mode has no cluster legend, so hiding state must not
            // leak in and darken the network the user is examining.
            if (!personalGraph && p.clusterId != null && hiddenClusters.has(p.clusterId)) {
              opacity = 0.12;
            }
            // F2: dim points excluded by year filter — never the focus.
            if (!isFocus && sinceYear != null && (p.lastPublicationYear ?? 9999) < sinceYear) {
              opacity = 0.15;
            }
            const showStroke = isHover || isFocus;
            const showLabel =
              personalGraph &&
              showNames &&
              inNetwork &&
              (isFocus || isHover || coauthorPaperCount.has(p.id));
            // N1: semantic zoom label with decluttering
            let showSemanticLabel = false;
            if (semanticZoomActive && !showLabel) {
              if (
                !lastLabeledPos ||
                dist2(p, lastLabeledPos) > minWorldDist2
              ) {
                showSemanticLabel = true;
                lastLabeledPos = { x: p.x, y: p.y };
              }
            }
            return (
              <g key={p.id}>
                <title>{`${p.label}${p.institution ? ` — ${p.institution}` : ""}`}</title>
                {isFocus && (
                  <circle
                    cx={cx}
                    cy={cy}
                    r={r * 2}
                    fill="none"
                    stroke={FOCUS_RING_COLOR}
                    strokeWidth={1.4 * invScale}
                    opacity={0.5}
                    pointerEvents="none"
                  />
                )}
                {/* F1: pulse ring animation on focus change */}
                {isFocus && pulseKey && (
                  <circle
                    key={pulseKey}
                    cx={cx}
                    cy={cy}
                    r={r * 2}
                    fill="none"
                    stroke={FOCUS_RING_COLOR}
                    strokeWidth={2 * invScale}
                    className="people-scatter__pulse"
                    pointerEvents="none"
                  />
                )}
                <circle
                  data-scatter-point
                  cx={cx}
                  cy={cy}
                  r={r}
                  fill={isFocus ? FOCUS_COLOR : fill}
                  stroke={showStroke ? "#1c1917" : "none"}
                  strokeWidth={showStroke ? 1.2 * invScale : 0}
                  opacity={opacity}
                  onMouseEnter={() => setHovered(p)}
                  onMouseLeave={() =>
                    setHovered((h) => (h?.id === p.id ? null : h))
                  }
                  onClick={(e) => {
                    e.stopPropagation();
                    userPanned.current = true;
                    setTransform((t) =>
                      panToPointTransform(
                        p,
                        size,
                        t,
                        overviewScale.current,
                      ),
                    );
                    onFocus(p.id);
                  }}
                  className={`people-scatter__point${isFocus ? " people-scatter__point--focus" : ""}`}
                />
                {showLabel && (
                  <g
                    transform={`translate(${cx} ${cy}) scale(${invScale} ${-invScale})`}
                    pointerEvents="none"
                  >
                    <text
                      className={`people-scatter__node-label${isFocus ? " people-scatter__node-label--focus" : ""}`}
                      textAnchor="middle"
                      y={-(screenR + 5)}
                    >
                      {shortLabel(p.label)}
                    </text>
                  </g>
                )}
                {/* N1: semantic zoom label (non-personal, close zoom) */}
                {showSemanticLabel && (
                  <g
                    transform={`translate(${cx} ${cy}) scale(${invScale} ${-invScale})`}
                    pointerEvents="none"
                  >
                    <text
                      className="people-scatter__node-label"
                      textAnchor="middle"
                      y={-(screenR + 5)}
                    >
                      {shortLabel(p.label)}
                    </text>
                  </g>
                )}
              </g>
            );
          });
          })()}

          {/* Cluster label pills — F2: use computed member counts */}
          {!personalGraph &&
            clusters.map((c) => {
              // D4: hide label pill for hidden clusters
              if (hiddenClusters.has(c.id)) return null;
              const members = pointsByCluster.get(c.id) ?? [];
              if (members.length === 0) return null;
              const xs = members.map((p) => p.x);
              const ys = members.map((p) => p.y);
              const lx = (Math.min(...xs) + Math.max(...xs)) / 2;
              const ly = (Math.min(...ys) + Math.max(...ys)) / 2;
              // Defensive: a NaN centroid would poison the pill's transform.
              if (!Number.isFinite(lx) || !Number.isFinite(ly)) return null;
              const displayCount = clusterMemberCounts.get(c.id) ?? c.memberCount;
              return (
                <g
                  key={c.id}
                  className="people-scatter__cluster-label"
                  transform={`translate(${lx} ${ly}) scale(${invScale} ${-invScale})`}
                  tabIndex={0}
                  role="button"
                  aria-label="Click to zoom · Shift+click to hide this cluster"
                  onClick={(e) => {
                    e.stopPropagation();
                    // D4: Shift+click hides the cluster
                    if (e.shiftKey) {
                      setHiddenClusters((prev) => {
                        const next = new Set(prev);
                        next.add(c.id);
                        persistHiddenClusters(next);
                        return next;
                      });
                      return;
                    }
                    userPanned.current = true;
                    setTransform(
                      fitMembersTransform(members, size, transform.scale),
                    );
                  }}
                  onKeyDown={(e) => {
                    // D5: Enter zooms to this cluster
                    if (e.key === "Enter") {
                      e.preventDefault();
                      e.stopPropagation();
                      userPanned.current = true;
                      setTransform(
                        fitMembersTransform(members, size, transform.scale),
                      );
                    }
                    // D5: Space activates the same as Enter (ARIA button).
                    if (e.key === " " || e.key === "Spacebar") {
                      e.preventDefault();
                      e.stopPropagation();
                      userPanned.current = true;
                      setTransform(
                        fitMembersTransform(members, size, transform.scale),
                      );
                    }
                  }}
                  style={{ cursor: "pointer" }}
                >
                  {/* Fix round 2: transparent hit target so the pill is
                      clickable/tabbable but its large rect doesn't block
                      hover on points underneath. */}
                  <circle
                    cx={0}
                    cy={0}
                    r={20 * invScale}
                    fill="transparent"
                    style={{ pointerEvents: "auto" }}
                  />
                  <rect
                    x={-72}
                    y={-13}
                    width={144}
                    height={26}
                    rx={13}
                    fill="rgba(255,255,255,0.92)"
                    stroke={clusterColorSlot(c.colorSlot)}
                    strokeWidth={1.2}
                    pointerEvents="none"
                  />
                  <text
                    textAnchor="middle"
                    dy={4}
                    className="people-scatter__cluster-label-text"
                    pointerEvents="none"
                  >
                    {shortLabel(c.label, 24)} · {displayCount}
                  </text>
                </g>
              );
            })}
        </g>
        )}
      </svg>

      {hovered && (
        <div ref={tooltipRef} className="people-scatter__tooltip" role="status">
          <strong>{hovered.label}</strong>
          <span>
            {hovered.institution ?? "—"}
            {hovered.rank ? ` · ${humanRank(hovered.rank)}` : ""}
            {` · ${impactLabel(hovered.impact)}`}
            {hovered.clusterLabel ? ` · ${hovered.clusterLabel}` : ""}
            {personalGraph &&
              personFocus &&
              hovered.id !== personFocus &&
              coauthorPaperCount.get(hovered.id) != null && (
                <> · {coauthorPaperCount.get(hovered.id)} shared papers</>
              )}
          </span>
        </div>
      )}

      <div className="people-scatter__status">
        {loading && (import.meta.env.VITE_API_URL ? "connecting to API…" : "loading map…")}
        {error && (
          <span className="people-scatter__error">
            {"projection failed: " + error.message}
          </span>
        )}
        {!loading && !error && (
          <span>
            {personalGraph
              ? `${coauthorTies.length} coauthors highlighted`
              : `${points.length} researchers`}
            {!personalGraph && ` · ${data?.projection.algorithm ?? ""}`}
            <span className="people-scatter__hint">
              · scroll or pinch to zoom · drag to pan
            </span>
          </span>
        )}
      </div>
    </div>
  );
}

function humanRank(rank: string): string {
  return rank.replace(/_/g, " ");
}
