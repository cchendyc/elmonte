import { describe, expect, it } from "vitest";
import {
  emptySession,
  linkKey,
  mergePage,
  sessionReducer,
  type PeoplePage,
} from "./graphSession";

function page(offset: number, total: number, ids: string[]): PeoplePage {
  return {
    ownerId: "o:1",
    groupKey: "all",
    offset,
    total,
    items: ids.map((id) => ({ id, label: `Person ${id}` })),
  };
}

describe("linkKey", () => {
  it("is independent of endpoint order", () => {
    expect(linkKey("p:1", "o:2")).toBe(linkKey("o:2", "p:1"));
  });
});

describe("mergePage", () => {
  it("accumulates forward slices without duplicates", () => {
    const first = page(24, 60, ["p:1", "p:2"]);
    const second = page(48, 60, ["p:3", "p:2"]);
    const merged = mergePage(first, second);
    expect(merged.offset).toBe(48);
    expect(merged.items.map((x) => x.id)).toEqual(["p:1", "p:2", "p:3"]);
  });

  it("takes the incoming page when none existed", () => {
    const merged = mergePage(undefined, page(24, 60, ["p:1"]));
    expect(merged.items.map((x) => x.id)).toEqual(["p:1"]);
  });
});

describe("sessionReducer page action", () => {
  it("accumulates roster pages instead of replacing them", () => {
    let state = emptySession("o:1");
    state = sessionReducer(state, { type: "page", page: page(24, 60, ["p:1", "p:2"]) });
    state = sessionReducer(state, { type: "page", page: page(48, 60, ["p:3"]) });
    const key = "o:1::all";
    expect(state.pages[key].items.map((x) => x.id)).toEqual(["p:1", "p:2", "p:3"]);
    expect(state.pages[key].offset).toBe(48);
  });
});
