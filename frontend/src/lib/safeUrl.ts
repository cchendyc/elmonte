/**
 * URL safety for externally-sourced links.
 *
 * The data pipeline ingests `homepage_url` and ORCID values from faculty
 * directories and OpenAlex; anything that fails validation here is simply
 * not rendered as a link.  This blocks stored-XSS vectors like
 * `javascript:alert(1)` slipping in through the data path.
 */

/** Only http(s) absolute URLs are safe for `<a href>` — `javascript:`,
 * `data:`, `vbscript:` and anything non-parseable are rejected. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    return new URL(trimmed).toString();
  } catch {
    return null;
  }
}

/** Safe DOI resolver link.  Accepts either a bare DOI (`10.x/...`) or an
 * existing `https://doi.org/...` URL; everything else is rejected. */
export function doiUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  const bare = /^10\.\d{4,9}\/\S+$/i.exec(trimmed);
  const target = bare ? `https://doi.org/${bare[0]}` : safeHttpUrl(trimmed);
  if (!target) return null;
  try {
    const parsed = new URL(target);
    return parsed.hostname.toLowerCase() === "doi.org" ? parsed.toString() : null;
  } catch {
    return null;
  }
}

/** ORCID iD format: 4-4-4-3 groups, last character may be a check digit X.
 *  The checksum is validated so a typo is never rendered as a link. */
export function isOrcid(value: string | null | undefined): boolean {
  if (typeof value !== "string") return false;
  const match = /^(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$/i.exec(value);
  if (!match) return false;
  const normalized = match[1].toUpperCase();
  const digits = normalized.replaceAll("-", "").slice(0, -1);
  let total = 0;
  for (const digit of digits) {
    total = (total + Number(digit)) * 2;
  }
  const check = (12 - (total % 11)) % 11;
  const expected = check === 10 ? "X" : String(check);
  return normalized.endsWith(expected);
}
