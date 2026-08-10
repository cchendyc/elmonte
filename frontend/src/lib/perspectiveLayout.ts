/**
 * Pure polar-layout helpers for the perspective (ego) view.
 * r = asymmetric importance; theta = alter-alter community wedges.
 * No React — unit-testable.
 */

export interface PolarPoint {
  r: number; // 0 = focus, 1 = outer rim
  theta: number; // radians, 0 = +x axis
}

/** Importance [0,1] -> radius [inner, rim] with linear mapping. */
export function importanceToRadius(importance: number, inner = 0.08, rim = 1): number {
  return inner + (rim - inner) * (1 - Math.min(1, Math.max(0, importance)));
}

/**
 * Assign theta to each alter index: groups sorted by size (desc) get wedges
 * proportional to their member count; within a group, members are spread
 * with deterministic golden-angle spacing (no random jitter).
 */
export function groupThetas(
  groupOf: (index: number) => number,
  count: number,
): number[] {
  const byGroup = new Map<number, number[]>();
  for (let i = 0; i < count; i++) {
    const g = groupOf(i);
    const arr = byGroup.get(g) ?? [];
    arr.push(i);
    byGroup.set(g, arr);
  }
  const groups = [...byGroup.entries()].sort((a, b) => b[1].length - a[1].length);
  const theta = new Array<number>(count).fill(0);
  const GOLDEN = (Math.sqrt(5) - 1) / 2;
  let start = 0;
  for (const [, members] of groups) {
    const wedge = (members.length / count) * Math.PI * 2;
    members.forEach((idx, k) => {
      const frac = members.length === 1 ? 0.5 : (k + 0.5) / members.length;
      // golden-angle wobble inside the wedge keeps close pairs from stacking
      const wobble = (k * GOLDEN * 0.3 - 0.15) * (wedge / members.length);
      theta[idx] = start + frac * wedge + wobble;
    });
    start += wedge;
  }
  return theta;
}

export function polarToCartesian(
  center: { x: number; y: number },
  p: PolarPoint,
  radiusPx: number,
): { x: number; y: number } {
  return {
    x: center.x + p.r * radiusPx * Math.cos(p.theta),
    y: center.y + p.r * radiusPx * Math.sin(p.theta),
  };
}
