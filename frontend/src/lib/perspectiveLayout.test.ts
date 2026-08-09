import { describe, expect, it } from "vitest";
import {
  groupThetas,
  importanceToRadius,
  polarToCartesian,
} from "./perspectiveLayout";

describe("importanceToRadius", () => {
  it("maps top importance to inner ring and zero to the rim", () => {
    expect(importanceToRadius(1)).toBeCloseTo(0.08);
    expect(importanceToRadius(0)).toBeCloseTo(1);
    expect(importanceToRadius(0.5)).toBeCloseTo(0.54);
  });
});

describe("groupThetas", () => {
  it("keeps group members in contiguous wedges sorted by size", () => {
    // alters 0..4 in group A (5 members), 5..6 in group B (2 members)
    // A gets the bigger wedge: 5/7 * 2π ≈ 4.487 rad (> π)
    const theta = groupThetas((i) => (i < 5 ? 0 : 1), 7);
    const wedgeA = (5 / 7) * Math.PI * 2;
    // Group A members should be in the first wedge [0, wedgeA) with small
    // wobble tolerance.
    for (const t of theta.slice(0, 5)) {
      expect(t).toBeGreaterThanOrEqual(-0.3);
      expect(t).toBeLessThan(wedgeA + 0.3);
    }
    // Group B members should be in the second wedge [wedgeA, 2π).
    for (const t of theta.slice(5)) {
      expect(t).toBeGreaterThanOrEqual(wedgeA - 0.3);
      expect(t).toBeLessThanOrEqual(2 * Math.PI + 0.01);
    }
    expect(theta).toHaveLength(7);
  });

  it("is deterministic", () => {
    const a = groupThetas((i) => i % 2, 10);
    const b = groupThetas((i) => i % 2, 10);
    expect(a).toEqual(b);
  });
});

describe("polarToCartesian", () => {
  it("places r=0 at the center", () => {
    const p = polarToCartesian({ x: 10, y: 20 }, { r: 0, theta: 1.3 }, 100);
    expect(p.x).toBeCloseTo(10);
    expect(p.y).toBeCloseTo(20);
  });
});
