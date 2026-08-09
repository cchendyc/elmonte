/**
 * PeopleScatter — El Monte people map.
 *
 * Each point is a researcher projected by structural similarity. Closer points
 * are more related; size reflects impact. Color mode is user-selectable.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@apollo/client/react";
import type { ProjectionData, ProjectionPoint } from "../api/projection";
import { PROJECTION } from "../api/projection";
import type { PersonCoauthorTiesData } from "../api/coauthorTies";
import { PERSON_COAUTHOR_TIES } from "../api/coauthorTies";
import {
  buildCategoryColorMaps,
  buildLegend,
  FOCUS_COLOR,
  FOCUS_RING_COLOR,
  modeSubtitle,
  pointFill,
  pointOpacity,
  SCATTER_COLOR_MODES,
  type ScatterColorMode,
} from "../lib/scatterColor";

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
const NEAR_RADIUS = FOCUS_WORLD_RADIUS;
const COLOR_MODE_STORAGE_KEY = "elmonte-scatter-color-mode";
const SHOW_NAMES_STORAGE_KEY = "elmonte-scatter-show-names";

const TIE_COLOR = "#4338ca";
const TIE_COLOR_STRONG = "#1d4ed8";
const OUTSIDE_NETWORK_OPACITY = 0.42;

function radiusForImpactScreen(impact: number): number {
  const t = Math.pow(Math.max(0, Math.min(1, impact)), 0.65);
  return MIN_SCREEN_R + t * (MAX_SCREEN_R - MIN_SCREEN_R);
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
  try {
    return localStorage.getItem(SHOW_NAMES_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function shortLabel(label: string, max = 22): string {
  if (label.length <= max) return label;
  return `${label.slice(0, max - 1)}…`;
}

function loadColorMode(): ScatterColorMode {
  try {
    const raw = localStorage.getItem(COLOR_MODE_STORAGE_KEY);
    if (raw === "similarity" || raw === "institution" || raw === "focus") {
      return raw;
    }
    if (raw === "community") return "similarity";
  } catch {
    // ignore
  }
  return "similarity";
}

export function PeopleScatter({ focusId, onFocus, minHeight, className }: Props) {
  const { data, loading, error } = useQuery<ProjectionData>(PROJECTION, {
    fetchPolicy: "cache-first",
  });
  const points = data?.projection.points ?? [];
  const runId = data?.projection.runId;

  const personFocus =
    focusId?.startsWith("p:") === true ? focusId : null;

  const { data: tieData, loading: tiesLoading } = useQuery<PersonCoauthorTiesData>(PERSON_COAUTHOR_TIES, {
    variables: { personId: personFocus ?? "" },
    skip: personFocus == null,
    fetchPolicy: "cache-first",
  });
  const coauthorTies = tieData?.personCoauthorTies ?? [];

  const [colorMode, setColorMode] = useState<ScatterColorMode>(loadColorMode);
  const [showNames, setShowNames] = useState(loadShowNames);
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

  const cameraTargetKey = runId
    ? personFocus
      ? `focus:${personFocus}:${size.w}x${size.h}`
      : `fit:${runId}:${size.w}x${size.h}`
    : null;

  const similarityGroupCount = useMemo(() => {
    return new Set(
      points
        .map((p) => p.similarityGroup)
        .filter((g): g is number => g != null),
    ).size;
  }, [points]);

  const colorMaps = useMemo(() => buildCategoryColorMaps(points), [points]);

  const legend = useMemo(
    () => buildLegend(points, colorMode, colorMaps),
    [points, colorMode, colorMaps],
  );

  const onColorModeChange = useCallback((mode: ScatterColorMode) => {
    setColorMode(mode);
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
        setTransform(panToPointTransform(point, size, IDENTITY, overviewScale.current));
        return;
      }
    }
    setTransform(fitAllTransform(points, size));
  }, [personFocus, points, size]);

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

  const nearRadius2 = NEAR_RADIUS * NEAR_RADIUS;
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
  }, [points, isFar, personFocus, personalGraph, isNetworkMember]);

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
            {!personalGraph && (
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
            )}
          </div>
        </div>
        <span className="people-scatter__subtitle">
          {personalGraph
            ? `${coauthorTies.length} coauthors on map · line width = shared papers`
            : modeSubtitle(colorMode, focusId, similarityGroupCount)}
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
      </div>

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
        onDoubleClick={onDoubleClick}
      >
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
          {sortedPoints.map((p) => {
            const isFocus = personFocus != null && p.id === personFocus;
            const inNetwork = isNetworkMember(p.id);
            const outsideNetwork = personalGraphActive && !inNetwork;
            const far = personalGraphActive ? false : isFar(p);
            const isHover = hovered?.id === p.id;
            const baseScreenR =
              radiusForImpactScreen(p.impact) + (isHover ? 1.2 : 0);
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
            const showStroke = isHover || isFocus;
            const showLabel =
              personalGraph &&
              showNames &&
              inNetwork &&
              (isFocus || isHover || coauthorPaperCount.has(p.id));
            return (
              <g key={p.id}>
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
              </g>
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
            {` · ${impactLabel(hovered.impact)}`}
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
