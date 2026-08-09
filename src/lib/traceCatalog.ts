import type { OrgUnit } from "../api/queries";

export interface TraceCatalog {
  universities: OrgUnit[];
  childrenByParent: Record<string, OrgUnit[]>;
  unitById: Record<string, OrgUnit>;
}

export function emptyCatalog(): TraceCatalog {
  return { universities: [], childrenByParent: {}, unitById: {} };
}

export function catalogFromUnits(
  universities: OrgUnit[],
  childrenByParent: Record<string, OrgUnit[]>,
): TraceCatalog {
  const unitById: Record<string, OrgUnit> = {};
  for (const unit of universities) unitById[unit.id] = unit;
  for (const children of Object.values(childrenByParent)) {
    for (const unit of children) unitById[unit.id] = unit;
  }
  return { universities, childrenByParent, unitById };
}
