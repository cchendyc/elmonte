import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { CombinedGraphQLErrors } from "@apollo/client";
import {
  EXPAND,
  FETCH_PAGE,
  type ExpandData,
  type ExpandVars,
  type FetchPageData,
  type FetchPageVars,
} from "../api/queries";
import {
  emptySession,
  pageKey,
  sessionReducer,
  type SessionState,
} from "./graphSession";
import type { PositionRank } from "./positionRank";

/** Log graph-session errors for developers only — never ship console noise. */
function devLogError(...args: unknown[]): void {
  if (import.meta.env.DEV) console.error(...args);
}

export interface GraphSessionApi {
  session: SessionState;
  expand: (id: string, advanceTrail?: boolean) => Promise<void>;
  onSelect: (id: string) => void;
  onTraceSelect: (id: string) => void;
  onToggleGroup: (ownerId: string, key: string) => Promise<void>;
  onLoadMore: (ownerId: string, key: string) => Promise<void>;
  onFilter: (ownerId: string, rank?: PositionRank) => void;
  error: string | null;
}

export function useGraphSession(
  focusId: string,
  onFocus?: (id: string) => void,
): GraphSessionApi {
  const client = useApolloClient();
  const [session, dispatch] = useReducer(sessionReducer, focusId, emptySession);
  const inFlight = useRef<Set<string>>(new Set());
  // State, not a ref: mutations must trigger a re-render or errors can never
  // surface in the UI (H2).
  const [error, setError] = useState<string | null>(null);

  const expand = useCallback(
    async (id: string, advanceTrail = true) => {
      if (inFlight.current.has(id)) return;
      inFlight.current.add(id);
      dispatch({ type: "loading", id, value: true });
      try {
        const { data } = await client.query<ExpandData, ExpandVars>({
          query: EXPAND,
          variables: { id },
          fetchPolicy: "cache-first",
        });
        if (!data) throw new Error("expand returned no data");
        const res = data.expand;
        setError(null);
        dispatch({
          type: "merge",
          focusId: res.focusId,
          nodes: res.nodes,
          links: res.links,
          groups: res.groups,
          page: res.page,
          advanceTrail,
        });

        if (!advanceTrail) return;

        const focus = res.nodes.find((n) => n.id === res.focusId);
        if (focus?.kind === "org" && res.groups && res.groups.length > 0) {
          dispatch({
            type: "openGroup",
            ownerId: res.focusId,
            groupKey: res.groups[0].key,
          });
        } else if (focus?.kind === "person") {
          const anchor = res.links.find(
            (l) => l.relation === "placement" && l.target === focus.id,
          );
          if (anchor) {
            dispatch({
              type: "openGroup",
              ownerId: anchor.source,
              groupKey: undefined,
            });
          }
        }
      } catch (err) {
        setError(describeError(err));
        devLogError("[graphSession] expand failed", err);
      } finally {
        inFlight.current.delete(id);
        dispatch({ type: "loading", id, value: false });
      }
    },
    [client],
  );

  useEffect(() => {
    if (!focusId) return;
    if (session.nodes[focusId]?.expanded) {
      dispatch({ type: "focus", id: focusId });
      return;
    }
    void expand(focusId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);

  useEffect(() => {
    if (onFocus && session.focusId !== focusId) onFocus(session.focusId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.focusId]);

  const fetchPage = useCallback(
    async (ownerId: string, groupKey: string, offset: number) => {
      const { data } = await client.query<FetchPageData, FetchPageVars>({
        query: FETCH_PAGE,
        variables: { ownerId, groupKey, offset },
        fetchPolicy: "cache-first",
      });
      if (!data) throw new Error("pages returned no data");
      return data.pages;
    },
    [client],
  );

  useEffect(() => {
    if (!focusId) return;
    const node = session.nodes[focusId];
    if (node?.kind !== "org") return;
    const groupState = session.groups[focusId];
    const groupKey = groupState?.openKey ?? groupState?.groups[0]?.key;
    if (!groupKey) return;
    if (session.pages[pageKey(focusId, groupKey)]) return;

    const ownerId = focusId;
    const gid = `grp:${ownerId}:${groupKey}`;
    dispatch({ type: "loading", id: gid, value: true });
    void (async () => {
      try {
        const page = await fetchPage(ownerId, groupKey, 0);
        dispatch({ type: "page", page });
        dispatch({ type: "openGroup", ownerId, groupKey });
      } catch (err) {
        setError(describeError(err));
        devLogError("[graphSession] roster prefetch failed", err);
      } finally {
        dispatch({ type: "loading", id: gid, value: false });
      }
    })();
  }, [focusId, fetchPage, session.groups, session.nodes, session.pages]);

  const onSelect = useCallback(
    (id: string) => {
      const node = session.nodes[id];
      const isFocus = id === session.focusId;
      if (node?.expanded && isFocus) {
        dispatch({ type: "collapse", id });
        return;
      }
      if (node?.expanded) {
        dispatch({ type: "focus", id });
        return;
      }
      void expand(id);
    },
    [expand, session.focusId, session.nodes],
  );

  const onTraceSelect = useCallback(
    (id: string) => {
      if (id === session.focusId) return;
      const node = session.nodes[id];
      if (node?.expanded) {
        dispatch({ type: "focus", id });
        return;
      }
      dispatch({ type: "focus", id });
      void expand(id, false);
    },
    [expand, session.focusId, session.nodes],
  );

  const onToggleGroup = useCallback(
    async (ownerId: string, key: string) => {
      const openKey = session.groups[ownerId]?.openKey;
      if (openKey === key) {
        dispatch({ type: "openGroup", ownerId, groupKey: undefined });
        return;
      }
      dispatch({ type: "openGroup", ownerId, groupKey: key });
      if (session.pages[pageKey(ownerId, key)]) return;
      const gid = `grp:${ownerId}:${key}`;
      if (inFlight.current.has(gid)) return;
      inFlight.current.add(gid);
      dispatch({ type: "loading", id: gid, value: true });
      try {
        const page = await fetchPage(ownerId, key, 0);
        dispatch({ type: "page", page });
        setError(null);
      } catch (err) {
        setError(describeError(err));
        devLogError("[graphSession] fetchPage failed", err);
      } finally {
        inFlight.current.delete(gid);
        dispatch({ type: "loading", id: gid, value: false });
      }
    },
    [fetchPage, session.groups, session.pages],
  );

  const onLoadMore = useCallback(
    async (ownerId: string, key: string) => {
      const existing = session.pages[pageKey(ownerId, key)];
      if (!existing || existing.offset >= existing.total) return;
      const bandId = `band:${ownerId}:${key}`;
      if (inFlight.current.has(bandId)) return;
      inFlight.current.add(bandId);
      dispatch({ type: "loading", id: bandId, value: true });
      try {
        const page = await fetchPage(ownerId, key, existing.offset);
        dispatch({ type: "page", page });
        setError(null);
      } catch (err) {
        setError(describeError(err));
        devLogError("[graphSession] fetchPage failed", err);
      } finally {
        inFlight.current.delete(bandId);
        dispatch({ type: "loading", id: bandId, value: false });
      }
    },
    [fetchPage, session.pages],
  );

  const onFilter = useCallback((ownerId: string, rank?: PositionRank) => {
    dispatch({ type: "rankFilter", ownerId, rank });
  }, []);

  return {
    session,
    expand,
    onSelect,
    onTraceSelect,
    onToggleGroup,
    onLoadMore,
    onFilter,
    error,
  };
}

function describeError(err: unknown): string {
  if (err instanceof CombinedGraphQLErrors) {
    return err.errors.map((e) => e.message).join(" · ") || "graphql error";
  }
  if (err instanceof Error) return err.message;
  return "unknown error";
}
