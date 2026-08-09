/**
 * Color modes for the people map scatter plot.
 */

import type { ProjectionPoint } from "../api/projection";
import { institutionBrandColor, stablePaletteSlot } from "./institutionColors";

export type ScatterColorMode = "cluster" | "institution" | "focus";

export const SCATTER_COLOR_MODES: Array<{
  id: ScatterColorMode;
  label: string;
}> = [
  { id: "cluster", label: "Cluster" },
  { id: "institution", label: "Institution" },
  { id: "focus", label: "Focus" },
];

export const FOCUS_COLOR = "#f59e0b";
export const FOCUS_RING_COLOR = "#f59e0b";
const FAR_POINT_COLOR = "#d4d4d8";
const NEUTRAL_COLOR = "#c4c4cc";

/** Well-separated hues — small integer ids must not hash to adjacent greens. */
const PALETTE = [
  { h: 234, s: 62, l: 56 },
  { h: 8, s: 72, l: 52 },
  { h: 152, s: 52, l: 46 },
  { h: 38, s: 78, l: 52 },
  { h: 280, s: 58, l: 58 },
  { h: 195, s: 62, l: 48 },
  { h: 350, s: 68, l: 54 },
  { h: 85, s: 52, l: 46 },
  { h: 220, s: 58, l: 54 },
  { h: 25, s: 70, l: 50 },
  { h: 300, s: 55, l: 52 },
  { h: 120, s: 48, l: 44 },
] as const;

export interface CategoryColorMaps {
  institutionById: Map<string, string>;
  clusterById: Map<number, string>;
}

export interface PointColorInput {
  point: ProjectionPoint;
  mode: ScatterColorMode;
  focusId: string | null;
  isFar: boolean;
  isFocus: boolean;
  colors: CategoryColorMaps;
}

export interface LegendEntry {
  key: string;
  label: string;
  color: string;
  count: number;
}

function paletteColor(slot: number): string {
  const c = PALETTE[((slot % PALETTE.length) + PALETTE.length) % PALETTE.length];
  return `hsl(${c.h}, ${c.s}%, ${c.l}%)`;
}

export function clusterColorSlot(slot: number): string {
  return paletteColor(slot);
}

export function buildCategoryColorMaps(
  points: ProjectionPoint[],
  clusters: Array<{ id: number; colorSlot: number }>,
): CategoryColorMaps {
  const institutionById = new Map<string, string>();
  for (const point of points) {
    if (!point.institutionId || institutionById.has(point.institutionId)) {
      continue;
    }
    const brand = institutionBrandColor(point.institution);
    institutionById.set(
      point.institutionId,
      brand ?? paletteColor(stablePaletteSlot(point.institutionId)),
    );
  }

  const clusterById = new Map<number, string>();
  for (const c of clusters) {
    clusterById.set(c.id, paletteColor(c.colorSlot));
  }

  return { institutionById, clusterById };
}

export function colorForInstitution(
  institutionId: string | null,
  colors: CategoryColorMaps,
): string {
  if (!institutionId) return NEUTRAL_COLOR;
  return colors.institutionById.get(institutionId) ?? NEUTRAL_COLOR;
}

export function colorForCluster(
  clusterId: number | null,
  colors: CategoryColorMaps,
): string {
  if (clusterId == null) return NEUTRAL_COLOR;
  return colors.clusterById.get(clusterId) ?? paletteColor(clusterId);
}

export function pointFill({
  point,
  mode,
  focusId,
  isFar,
  isFocus,
  colors,
}: PointColorInput): string {
  if (isFocus) return FOCUS_COLOR;

  if (mode === "focus") {
    if (!focusId) return NEUTRAL_COLOR;
    if (isFar) return FAR_POINT_COLOR;
    if (point.clusterId != null) {
      return colorForCluster(point.clusterId, colors);
    }
    return colorForInstitution(point.institutionId, colors);
  }

  if (isFar) return FAR_POINT_COLOR;

  if (mode === "institution") {
    return colorForInstitution(point.institutionId, colors);
  }

  if (point.clusterId != null) {
    return colorForCluster(point.clusterId, colors);
  }
  return colorForInstitution(point.institutionId, colors);
}

export function pointOpacity({
  mode,
  focusId,
  isFar,
  isFocus,
  isHover,
}: Pick<PointColorInput, "mode" | "focusId" | "isFar" | "isFocus"> & {
  isHover: boolean;
}): number {
  if (isFocus || isHover) return 1;
  if (mode === "focus" && focusId && isFar) return 0.14;
  if (isFar) return 0.3;
  if (mode === "focus" && !focusId) return 0.72;
  return 0.88;
}

export function buildLegend(
  points: ProjectionPoint[],
  mode: ScatterColorMode,
  colors: CategoryColorMaps,
): LegendEntry[] {
  if (mode !== "institution") return [];

  const counts = new Map<string, LegendEntry>();

  for (const p of points) {
    const key = p.institutionId ?? "unknown";
    const label = p.institution ?? "Unknown";
    const color = colorForInstitution(p.institutionId, colors);
    const prev = counts.get(key);
    counts.set(key, {
      key,
      label,
      color,
      count: (prev?.count ?? 0) + 1,
    });
  }

  return [...counts.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, 6);
}

export function modeSubtitle(
  mode: ScatterColorMode,
  focusId: string | null,
  clusterCount: number,
): string {
  switch (mode) {
    case "cluster":
      return clusterCount > 0
        ? `${clusterCount} clusters · size = publications & citations`
        : "Color = cluster · size = publications & citations";
    case "institution":
      return "Color = institution · size = publications & citations";
    case "focus":
      return focusId
        ? "Highlighting focused neighborhood"
        : "Select a person to highlight their neighborhood";
    default:
      return "Closer = more related · larger = more impact";
  }
}
