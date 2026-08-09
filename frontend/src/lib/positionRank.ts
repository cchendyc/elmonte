/**
 * Classifies a free-text job title into one of a small number of buckets. The
 * ingest emits titles like "Assistant Professor of Economics" or "Postdoctoral
 * Scholar"; the chart needs coarse categories to filter by, and the DB has an
 * `enum position_rank` that mirrors these. Kept as a single source of truth so
 * the filter pills and any future loader agree on the mapping.
 */

export type PositionRank =
  | "full_professor"
  | "associate_professor"
  | "assistant_professor"
  | "adjunct_professor"
  | "emeritus_professor"
  | "lecturer"
  | "postdoc"
  | "research_scientist"
  | "research_fellow"
  | "visiting";

/** Human labels for filter pills, in the order they should appear. */
export const RANK_LABELS: Array<{ rank: PositionRank; label: string }> = [
  { rank: "full_professor", label: "Professor" },
  { rank: "associate_professor", label: "Assoc. Prof" },
  { rank: "assistant_professor", label: "Asst. Prof" },
  { rank: "adjunct_professor", label: "Adjunct" },
  { rank: "emeritus_professor", label: "Emeritus" },
  { rank: "lecturer", label: "Lecturer" },
  { rank: "postdoc", label: "Postdoc" },
  { rank: "research_scientist", label: "Researcher" },
  { rank: "research_fellow", label: "Fellow" },
  { rank: "visiting", label: "Visiting" },
];

const RANK_ORDER = new Map<PositionRank, number>(
  RANK_LABELS.map((entry, index) => [entry.rank, index]),
);

export function rankOrder(rank: PositionRank | undefined): number {
  return rank ? (RANK_ORDER.get(rank) ?? RANK_LABELS.length) : RANK_LABELS.length;
}

// Ordered longest / most-specific first so "assistant professor" wins before
// the bare "professor" and "postdoctoral" beats "post".
const PATTERNS: Array<[RegExp, PositionRank]> = [
  [/emeritus|emerita/i, "emeritus_professor"],
  [/adjunct/i, "adjunct_professor"],
  [/assistant\s+professor/i, "assistant_professor"],
  [/associate\s+professor/i, "associate_professor"],
  [/visiting/i, "visiting"],
  [/lecturer|instructor/i, "lecturer"],
  [/post[-\s]?doc(toral)?|postdoctoral/i, "postdoc"],
  [/predoctoral|predoc|research\s+fellow|senior\s+fellow/i, "research_fellow"],
  [/research\s+scientist|staff\s+scientist/i, "research_scientist"],
  [/\bprofessor\b|\bprof\b/i, "full_professor"],
];

export function classifyRank(title: string | undefined | null): PositionRank | undefined {
  if (!title) return undefined;
  for (const [pattern, rank] of PATTERNS) {
    if (pattern.test(title)) return rank;
  }
  return undefined;
}

export function rankLabel(rank: PositionRank): string {
  return RANK_LABELS.find((entry) => entry.rank === rank)?.label ?? rank;
}
