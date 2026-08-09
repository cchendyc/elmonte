/**
 * Pure layout helpers for the atlas scatter: cluster hulls, edge scaling,
 * curved edge paths. No React, no network — unit-testable.
 */

export interface XY {
  x: number;
  y: number;
}

/** Andrew's monotone chain convex hull; returns CCW polygon. */
export function convexHull(points: XY[]): XY[] {
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (pts.length <= 2) return pts;
  const cross = (o: XY, a: XY, b: XY) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: XY[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper: XY[] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/** Hull polygon per cluster id; small clusters fall back to a 12-gon around the centroid. */
export function clusterHulls(
  points: Array<XY & { clusterId: number | null }>,
  clusters: Array<{ id: number; cx: number; cy: number }>,
): Map<number, XY[]> {
  const byCluster = new Map<number, XY[]>();
  for (const p of points) {
    if (p.clusterId == null) continue;
    const arr = byCluster.get(p.clusterId) ?? [];
    arr.push({ x: p.x, y: p.y });
    byCluster.set(p.clusterId, arr);
  }
  const out = new Map<number, XY[]>();
  for (const c of clusters) {
    const members = byCluster.get(c.id) ?? [];
    if (members.length < 3) {
      const r = 0.05 + 0.012 * members.length;
      const poly: XY[] = [];
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2;
        poly.push({ x: c.cx + r * Math.cos(a), y: c.cy + r * Math.sin(a) });
      }
      out.set(c.id, poly);
    } else {
      out.set(c.id, convexHull(members));
    }
  }
  return out;
}

/** Weight → stroke width factor in [0, 1]. */
export function edgeWidthScale(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 0;
  return Math.max(0, Math.min(1, weight / maxWeight));
}

/** Minimum squared Euclidean distance between any two points.
 *
 * Returns ``Infinity`` for fewer than two points.  O(n²) — callers gate this
 * on dataset size (the initial-fit separation guarantee only applies to
 * small-to-medium maps where an unreadable blob is actually a risk).
 */
export function minPairDistSq(points: XY[]): number {
  let best = Infinity;
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const dx = points[i].x - points[j].x;
      const dy = points[i].y - points[j].y;
      const d2 = dx * dx + dy * dy;
      if (d2 < best) best = d2;
    }
  }
  return best;
}

/** Centroid of the cluster with the most members; null when no cluster has ≥2.
 *
 * Used to center the initial camera on the densest region when the fit
 * zooms past the global bounding box (sparse maps with one big cluster).
 */
export function densestClusterCentroid(
  points: Array<XY & { clusterId: number | null }>,
): XY | null {
  const byCluster = new Map<number, XY[]>();
  for (const p of points) {
    if (p.clusterId == null) continue;
    const arr = byCluster.get(p.clusterId);
    if (arr) arr.push(p);
    else byCluster.set(p.clusterId, [p]);
  }
  let best: XY[] = [];
  for (const arr of byCluster.values()) {
    if (arr.length > best.length) best = arr;
  }
  if (best.length < 2) return null;
  return {
    x: best.reduce((s, p) => s + p.x, 0) / best.length,
    y: best.reduce((s, p) => s + p.y, 0) / best.length,
  };
}

/** Quadratic bezier with a perpendicular sag for inter-cluster edges. */
export function curvedEdgePath(a: XY, b: XY, sag: number): string {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len === 0) {
    // Degenerate self-edge — render a zero-length path instead of dividing
    // by zero (which would put the control point at Infinity).
    return `M ${a.x} ${a.y} L ${a.x} ${a.y}`;
  }
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const px = -dy / len;
  const py = dx / len;
  const qx = mx + px * sag;
  const qy = my + py * sag;
  return `M ${a.x} ${a.y} Q ${qx} ${qy} ${b.x} ${b.y}`;
}
