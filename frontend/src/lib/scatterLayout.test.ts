import { describe, expect, it } from "vitest";
import {
  clusterHulls,
  convexHull,
  curvedEdgePath,
  densestClusterCentroid,
  edgeWidthScale,
  minPairDistSq,
} from "./scatterLayout";

describe("convexHull", () => {
  it("returns the 4 corners of a square", () => {
    const hull = convexHull([
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
      { x: 0.5, y: 0.5 },
    ]);
    expect(hull).toHaveLength(4);
  });
});

describe("clusterHulls", () => {
  it("falls back to a polygon for tiny clusters", () => {
    const hulls = clusterHulls(
      [
        { x: 0, y: 0, clusterId: 1 },
        { x: 0.1, y: 0, clusterId: 1 },
      ],
      [{ id: 1, cx: 0.05, cy: 0 }],
    );
    expect(hulls.get(1)!.length).toBe(12);
  });
});

describe("edgeWidthScale", () => {
  it("normalizes weight to 0..1", () => {
    expect(edgeWidthScale(2, 10)).toBeCloseTo(0.2);
    expect(edgeWidthScale(0, 10)).toBe(0);
    expect(edgeWidthScale(5, 0)).toBe(0);
  });
});

describe("curvedEdgePath", () => {
  it("produces a bezier through the sag point", () => {
    const path = curvedEdgePath({ x: 0, y: 0 }, { x: 2, y: 0 }, 0.5);
    expect(path.startsWith("M 0 0 Q")).toBe(true);
    expect(path).toContain("Q 1 0.5");
  });

  it("degenerates to a zero-length path for a self-edge", () => {
    const path = curvedEdgePath({ x: 1, y: 1 }, { x: 1, y: 1 }, 0.5);
    expect(path).toBe("M 1 1 L 1 1");
  });
});

describe("minPairDistSq", () => {
  it("finds the closest pair", () => {
    expect(
      minPairDistSq([
        { x: 0, y: 0 },
        { x: 3, y: 4 },
        { x: 0, y: 1 },
      ]),
    ).toBeCloseTo(1);
  });

  it("is Infinity for fewer than two points", () => {
    expect(minPairDistSq([{ x: 0, y: 0 }])).toBe(Infinity);
    expect(minPairDistSq([])).toBe(Infinity);
  });
});

describe("densestClusterCentroid", () => {
  it("returns the centroid of the biggest cluster", () => {
    const centroid = densestClusterCentroid([
      { x: 0, y: 0, clusterId: 1 },
      { x: 0, y: 2, clusterId: 1 },
      { x: 10, y: 0, clusterId: 2 },
      { x: 10, y: 0, clusterId: 2 },
      { x: 10, y: 2, clusterId: 2 },
      { x: 50, y: 50, clusterId: null },
    ]);
    expect(centroid).toEqual({ x: 10, y: 2 / 3 });
  });

  it("returns null when no cluster has two members", () => {
    expect(
      densestClusterCentroid([
        { x: 0, y: 0, clusterId: 1 },
        { x: 5, y: 5, clusterId: null },
      ]),
    ).toBeNull();
  });
});
