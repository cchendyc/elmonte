import type { OrgUnit } from "../api/queries";
import { type PagePerson, type SessionNode, type SessionState } from "./graphSession";
import type { TraceCatalog } from "./traceCatalog";

export interface TraceStep {
  id: string;
  index: number;
  options: string[];
  /** Empty id — user still needs to pick at this level. */
  pending?: boolean;
  placeholder?: string;
}

const ORG_DEPTH: Record<string, number> = {
  university: 0,
  school: 1,
  department: 2,
  lab: 3,
  institute: 3,
  company: 4,
  funder: 4,
  nonprofit: 4,
  government: 4,
  consortium: 4,
  publisher: 4,
};

function orgDepth(orgKind?: string): number {
  return ORG_DEPTH[orgKind ?? ""] ?? 5;
}

function isPersonNode(
  node: SessionNode | OrgUnit | undefined,
): node is SessionNode {
  return !!node && "kind" in node && node.kind === "person";
}

function isOrgUnit(
  node: SessionNode | OrgUnit | undefined,
): node is SessionNode | OrgUnit {
  return !!node && !isPersonNode(node);
}

function sortIds(
  state: SessionState,
  catalog: TraceCatalog,
  ids: string[],
): string[] {
  return [...ids].sort((a, b) => {
    const na = resolveNode(state, catalog, a);
    const nb = resolveNode(state, catalog, b);
    if (!na || !nb) return a.localeCompare(b);
    if (isPersonNode(na) !== isPersonNode(nb)) {
      return isPersonNode(na) ? 1 : -1;
    }
    if (isOrgUnit(na) && isOrgUnit(nb)) {
      const d = orgDepth(na.orgKind) - orgDepth(nb.orgKind);
      if (d !== 0) return d;
    }
    return na.label.localeCompare(nb.label);
  });
}

function parentOrgId(state: SessionState, orgId: string): string | null {
  for (const link of Object.values(state.links)) {
    if (link.relation === "org_parent" && link.target === orgId) {
      return link.source;
    }
  }
  return null;
}

function childOrgIds(state: SessionState, orgId: string): string[] {
  return Object.values(state.links)
    .filter((link) => link.relation === "org_parent" && link.source === orgId)
    .map((link) => link.target)
    .filter((id) => state.nodes[id]?.kind === "org");
}

function catalogChildIds(catalog: TraceCatalog, parentId: string): string[] {
  return (catalog.childrenByParent[parentId] ?? []).map((unit) => unit.id);
}

function mergeOptionIds(
  state: SessionState,
  catalog: TraceCatalog,
  ...groups: string[][]
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const group of groups) {
    for (const id of group) {
      if (!id || seen.has(id)) continue;
      seen.add(id);
      out.push(id);
    }
  }
  return sortIds(state, catalog, out);
}

function placementOrgId(state: SessionState, personId: string): string | null {
  for (const link of Object.values(state.links)) {
    if (link.relation === "placement" && link.target === personId) {
      return link.source;
    }
  }
  return null;
}

function childPersonIds(state: SessionState, orgId: string): string[] {
  const ids = new Set<string>();
  for (const link of Object.values(state.links)) {
    if (link.relation === "placement" && link.source === orgId) {
      ids.add(link.target);
    }
  }
  for (const page of Object.values(state.pages)) {
    if (page.ownerId !== orgId) continue;
    for (const item of page.items) ids.add(item.id);
  }
  return [...ids];
}

function advisorId(state: SessionState, personId: string): string | null {
  for (const link of Object.values(state.links)) {
    if (link.relation === "report" && link.target === personId) {
      return link.source;
    }
  }
  return null;
}

function adviseeIds(state: SessionState, personId: string): string[] {
  return Object.values(state.links)
    .filter((link) => link.relation === "report" && link.source === personId)
    .map((link) => link.target)
    .filter((id) => state.nodes[id]?.kind === "person");
}

function orgAncestryIds(state: SessionState, orgId: string): string[] {
  const chain: string[] = [];
  let cur: string | null = orgId;
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    chain.unshift(cur);
    cur = parentOrgId(state, cur);
  }
  return chain;
}

function personSupervisionChain(state: SessionState, personId: string): string[] {
  const chain: string[] = [personId];
  let cur = personId;
  const seen = new Set<string>([personId]);
  while (true) {
    const advisor = advisorId(state, cur);
    if (!advisor || seen.has(advisor)) break;
    chain.unshift(advisor);
    seen.add(advisor);
    cur = advisor;
  }
  return chain;
}

function isUniversityNode(
  state: SessionState,
  catalog: TraceCatalog,
  id: string,
): boolean {
  const node = resolveNode(state, catalog, id);
  if (!node) return false;
  if ("kind" in node && node.kind === "person") return false;
  return "orgKind" in node && node.orgKind === "university";
}

function personFromPages(state: SessionState, id: string): PagePerson | undefined {
  for (const page of Object.values(state.pages)) {
    const item = page.items.find((row) => row.id === id);
    if (item) return item;
  }
  return undefined;
}

function pendingChildOptions(
  state: SessionState,
  catalog: TraceCatalog,
  orgId: string,
): string[] {
  return mergeOptionIds(
    state,
    catalog,
    catalogChildIds(catalog, orgId),
    childOrgIds(state, orgId),
    childPersonIds(state, orgId),
  );
}

function pendingStepLabel(
  orgKind: string | undefined,
  orgCount: number,
  peopleCount: number,
): string {
  if (orgCount > 0 && peopleCount > 0) return "Select unit or researcher";
  if (peopleCount > 0) return "Select researcher";
  return nextOrgLevelLabel(orgKind);
}

function nextOrgLevelLabel(parentOrgKind?: string): string {
  switch (parentOrgKind) {
    case "university":
      return "Select school or department";
    case "school":
      return "Select department or lab";
    case "department":
      return "Select lab or unit";
    default:
      return "Select next unit";
  }
}

/** Canonical top-down path from university to the current focus. */
export function buildTopDownPath(state: SessionState): string[] {
  const focus = state.nodes[state.focusId];
  if (!focus) return [];

  if (focus.kind === "org") {
    return orgAncestryIds(state, state.focusId);
  }

  const anchorOrg = placementOrgId(state, state.focusId);
  const orgPart = anchorOrg ? orgAncestryIds(state, anchorOrg) : [];
  const personPart = personSupervisionChain(state, state.focusId);
  return [...orgPart, ...personPart];
}

function optionsForStep(
  state: SessionState,
  catalog: TraceCatalog,
  path: string[],
  index: number,
): string[] {
  const id = path[index];
  const node = resolveNode(state, catalog, id);
  if (!node) return id ? [id] : catalog.universities.map((u) => u.id);

  if (isOrgUnit(node) && isUniversityNode(state, catalog, id)) {
    return mergeOptionIds(
      state,
      catalog,
      catalog.universities.map((u) => u.id),
      [id],
    );
  }

  const parentId = index > 0 ? path[index - 1] : null;
  if (!parentId) {
    return mergeOptionIds(
      state,
      catalog,
      catalog.universities.map((u) => u.id),
      [id],
    );
  }

  const parent = resolveNode(state, catalog, parentId);
  if (!parent) return [id];

  if (isOrgUnit(parent) && isOrgUnit(node)) {
    return mergeOptionIds(
      state,
      catalog,
      catalogChildIds(catalog, parentId),
      childOrgIds(state, parentId),
      [id],
    );
  }

  if (isOrgUnit(parent) && isPersonNode(node)) {
    return mergeOptionIds(state, catalog, childPersonIds(state, parentId), [id]);
  }

  if (isPersonNode(parent) && isPersonNode(node)) {
    return mergeOptionIds(state, catalog, adviseeIds(state, parentId), [id]);
  }

  return [id];
}

export function buildTopDownTrace(
  state: SessionState,
  catalog: TraceCatalog,
): TraceStep[] {
  const path = buildTopDownPath(state);
  const steps: TraceStep[] = path.map((id, index) => ({
    id,
    index,
    options: optionsForStep(state, catalog, path, index),
  }));

  if (path.length === 0) {
    return [
      {
        id: "",
        index: 0,
        options: catalog.universities.map((u) => u.id),
        pending: true,
        placeholder: "Select university",
      },
    ];
  }

  const lastId = path[path.length - 1];
  const last = resolveNode(state, catalog, lastId);
  if (isOrgUnit(last) && state.focusId === lastId) {
    const orgChildren = mergeOptionIds(
      state,
      catalog,
      catalogChildIds(catalog, lastId),
      childOrgIds(state, lastId),
    );
    const people = childPersonIds(state, lastId);
    const combined = pendingChildOptions(state, catalog, lastId);
    if (combined.length > 0) {
      steps.push({
        id: "",
        index: steps.length,
        options: combined,
        pending: true,
        placeholder: pendingStepLabel(last.orgKind, orgChildren.length, people.length),
      });
    }
  }

  return steps;
}

export function nextTraceOptions(
  state: SessionState,
  catalog: TraceCatalog,
): string[] {
  const path = buildTopDownPath(state);
  const lastId = path[path.length - 1] ?? state.focusId;
  const last = resolveNode(state, catalog, lastId);
  if (!last) return [];

  const inPath = new Set(path);

  if (isOrgUnit(last)) {
    const orgChildren = mergeOptionIds(
      state,
      catalog,
      catalogChildIds(catalog, lastId),
      childOrgIds(state, lastId),
    );
    const people = childPersonIds(state, lastId);
    return mergeOptionIds(state, catalog, orgChildren, people).filter(
      (id) => !inPath.has(id),
    );
  }

  return adviseeIds(state, lastId).filter((id) => !inPath.has(id));
}

export function orgParentsToPrefetch(
  state: SessionState,
  catalog: TraceCatalog,
): string[] {
  const ids = new Set<string>();
  for (const id of buildTopDownPath(state)) {
    if (state.nodes[id]?.kind === "org" || catalog.unitById[id]) {
      ids.add(id);
    }
  }
  const focus = state.nodes[state.focusId];
  if (focus?.kind === "org") ids.add(state.focusId);
  return [...ids];
}

export function resolveNode(
  state: SessionState,
  catalog: TraceCatalog,
  id: string,
): SessionNode | OrgUnit | undefined {
  if (!id) return undefined;
  const sessionNode = state.nodes[id];
  if (sessionNode) return sessionNode;
  const catalogUnit = catalog.unitById[id];
  if (catalogUnit) return catalogUnit;
  const pagePerson = personFromPages(state, id);
  if (pagePerson) {
    return {
      id: pagePerson.id,
      kind: "person",
      label: pagePerson.label,
      sublabel: pagePerson.sublabel,
      rank: pagePerson.rank,
      step: 0,
      expanded: false,
    };
  }
  return undefined;
}

export function stepKindLabel(
  node: SessionNode | OrgUnit | undefined,
): string {
  if (!node) return "Node";
  if ("kind" in node && node.kind === "person") return "Researcher";
  const orgKind = "orgKind" in node ? node.orgKind : undefined;
  if (!orgKind) return "Organization";
  return orgKind.charAt(0).toUpperCase() + orgKind.slice(1);
}

export function stepMeta(
  node: SessionNode | OrgUnit | undefined,
  session: SessionState,
  id: string,
): string {
  if (!node) return "";
  if ("kind" in node && node.kind === "person") {
    return node.sublabel ?? node.institution ?? "Researcher";
  }
  if (node.sublabel) return node.sublabel;
  const groups = session.groups[id]?.groups;
  if (groups && groups.length > 0) {
    const kind = stepKindLabel(node);
    const total = groups.reduce((sum, group) => sum + group.count, 0);
    if (node.orgKind === "university") {
      return `${kind} · ${groups.length} school${groups.length === 1 ? "" : "s"}`;
    }
    if (node.orgKind === "school") {
      return `${kind} · ${groups.length} unit${groups.length === 1 ? "" : "s"}`;
    }
    return `${kind} · ${total} people`;
  }
  return stepKindLabel(node);
}
