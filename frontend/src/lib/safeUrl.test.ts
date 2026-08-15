import { describe, expect, it } from "vitest";
import { doiUrl, isOrcid, safeHttpUrl } from "./safeUrl";

describe("safeHttpUrl", () => {
  it("accepts http and https URLs", () => {
    expect(safeHttpUrl("https://example.edu/~person")).toBe("https://example.edu/~person");
    expect(safeHttpUrl("HTTP://EXAMPLE.EDU")).toBe("http://example.edu/");
  });

  it("rejects javascript and data URLs", () => {
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("data:text/html,hello")).toBeNull();
  });
});

describe("isOrcid", () => {
  it("accepts a valid ORCID with its check digit", () => {
    expect(isOrcid("0000-0001-5000-0007")).toBe(true);
    expect(isOrcid("https://orcid.org/0000-0002-1825-0097")).toBe(false);
  });

  it("rejects a wrong check digit", () => {
    expect(isOrcid("0000-0001-5000-0008")).toBe(false);
  });

  it("accepts a valid X check digit in either case", () => {
    expect(isOrcid("0000-0000-0000-001X")).toBe(true);
    expect(isOrcid("0000-0000-0000-001x")).toBe(true);
  });
});


describe("doiUrl", () => {
  it("wraps a bare DOI in the doi.org resolver", () => {
    expect(doiUrl("10.1145/1234")).toBe("https://doi.org/10.1145/1234");
  });

  it("accepts an existing https doi.org URL", () => {
    expect(doiUrl("https://doi.org/10.1000/xyz")).toBe("https://doi.org/10.1000/xyz");
  });

  it("rejects non-DOI hosts and unsafe values", () => {
    expect(doiUrl("https://example.com/10.1000/xyz")).toBeNull();
    expect(doiUrl("javascript:alert(1)")).toBeNull();
    expect(doiUrl("not-a-doi")).toBeNull();
  });
});
