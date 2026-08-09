/**
 * Fixed university brand colors for the people map.
 * Matched by institution display name from the projection API.
 */

/** Exact names from org_tree university ancestors (longest / most specific first). */
const INSTITUTION_BRAND_BY_NAME: Readonly<Record<string, string>> = {
  "University of California, Berkeley": "#003262",
  "Stanford University": "#8C1515",
  "Harvard University": "#A51C30",
  "Massachusetts Institute of Technology": "#A31F34",
  "Yale University": "#00356B",
  "Princeton University": "#E77500",
  "University of Chicago": "#800000",
  "Northwestern University": "#4E2A84",
  "Columbia University": "#0074B3",
  "Duke University": "#003087",
  "Cornell University": "#B31B1B",
  "New York University": "#57068C",
  "University of Michigan": "#00274C",
  "University of Southern California": "#990000",
  "Carnegie Mellon University": "#C41230",
  "Brown University": "#4E3629",
  "Dartmouth College": "#00693E",
  "Georgetown University": "#041E42",
  "Boston University": "#CC0000",
  "University of Pennsylvania": "#011F5B",
  "Johns Hopkins University": "#002D72",
  "University of California San Diego": "#182B49",
  "University of Virginia": "#232D4B",
  "University of Minnesota": "#7A0019",
  "Indiana University": "#990000",
  "Pennsylvania State University": "#041E42",
  "Michigan State University": "#18453B",
  "Vanderbilt University": "#866D4B",
  "University of Notre Dame": "#0C2340",
  "University of Cambridge": "#A3C1AD",
  "University of Oxford": "#002147",
  "National University of Singapore": "#003D7C",
  "Tsinghua University": "#82318E",
  "Peking University": "#94070A",
  "London School of Economics": "#E41F13",
  "INSEAD": "#0B1F8F",
};

/** Substring fallbacks when OpenAlex / roster labels differ slightly. */
const INSTITUTION_BRAND_PATTERNS: ReadonlyArray<{
  pattern: RegExp;
  color: string;
}> = [
  { pattern: /university of california,\s*berkeley|uc berkeley/i, color: "#003262" },
  { pattern: /stanford/i, color: "#8C1515" },
  { pattern: /harvard/i, color: "#A51C30" },
  { pattern: /massachusetts institute of technology|\bmit\b/i, color: "#A31F34" },
  { pattern: /yale/i, color: "#00356B" },
  { pattern: /princeton/i, color: "#E77500" },
  { pattern: /university of chicago|uchicago/i, color: "#800000" },
  { pattern: /northwestern/i, color: "#4E2A84" },
  { pattern: /columbia university/i, color: "#0074B3" },
];

export function institutionBrandColor(
  institutionName: string | null | undefined,
): string | null {
  if (!institutionName) return null;

  const exact = INSTITUTION_BRAND_BY_NAME[institutionName];
  if (exact) return exact;

  for (const { pattern, color } of INSTITUTION_BRAND_PATTERNS) {
    if (pattern.test(institutionName)) return color;
  }

  return null;
}

/** Stable palette slot for schools without a brand mapping. */
export function stablePaletteSlot(key: string): number {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  return Math.abs(hash);
}
