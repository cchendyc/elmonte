import { useCallback, useEffect, useMemo, useState } from "react";
import { useApolloClient, useQuery } from "@apollo/client/react";
import {
  ORG_CHILDREN,
  UNIVERSITIES,
  type OrgChildrenData,
  type OrgChildrenVars,
  type OrgUnit,
  type UniversitiesData,
} from "../api/queries";
import type { SessionState } from "./graphSession";
import {
  catalogFromUnits,
  type TraceCatalog,
} from "./traceCatalog";
import { orgParentsToPrefetch } from "./tracePath";

export function useTraceCatalog(session: SessionState) {
  const client = useApolloClient();
  const { data: uniData, loading: uniLoading } = useQuery<UniversitiesData>(
    UNIVERSITIES,
    { fetchPolicy: "cache-first" },
  );
  const [childrenByParent, setChildrenByParent] = useState<
    Record<string, OrgUnit[]>
  >({});

  const universities = uniData?.universities ?? [];

  const catalog: TraceCatalog = useMemo(
    () => catalogFromUnits(universities, childrenByParent),
    [universities, childrenByParent],
  );

  const ensureChildren = useCallback(
    async (parentId: string) => {
      if (!parentId) return;
      const { data } = await client.query<OrgChildrenData, OrgChildrenVars>({
        query: ORG_CHILDREN,
        variables: { parentId },
        fetchPolicy: "cache-first",
      });
      if (!data?.orgChildren) return;
      setChildrenByParent((prev) => {
        if (prev[parentId]) return prev;
        return { ...prev, [parentId]: data.orgChildren };
      });
    },
    [client],
  );

  useEffect(() => {
    for (const parentId of orgParentsToPrefetch(session, catalog)) {
      void ensureChildren(parentId);
    }
  }, [session, catalog, ensureChildren]);

  return {
    catalog,
    loading: uniLoading,
    ensureChildren,
  };
}

export type { TraceCatalog } from "./traceCatalog";
export { emptyCatalog } from "./traceCatalog";
