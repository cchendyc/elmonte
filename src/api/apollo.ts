/**
 * Apollo Client — the ONLY way the app talks to the backend.
 *
 * Cache policy:
 *
 *   * `search(q,limit)` — keyed by args. Repeated searches hit the cache
 *     until the underlying data changes (i.e. never during a session).
 *
 *   * `expand(id, on)` / `pages(ownerId, groupKey, offset, limit, on)` —
 *     also keyed by args. Cheap replays when a user re-focuses a node they
 *     already opened. Node/link merging into the accumulating session graph
 *     is the reducer's job (see `graphSession.ts`); Apollo is just a wire
 *     cache.
 *
 * `HopNode`, `HopLink`, `PagePerson`, `HopResponse`, `PeoplePage` are marked
 * `keyFields: false` because they carry no persistent identity — the same
 * person can appear in many query results, and we don't want Apollo to
 * dedupe them across queries (that would confuse the reducer's `stub` /
 * `via` bookkeeping).
 */

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";

const apiUrl = import.meta.env.VITE_API_URL?.replace(/\/$/, "");
const graphqlUri = apiUrl ? `${apiUrl}/graphql` : "/api/graphql";

const httpLink = new HttpLink({
  uri: graphqlUri,
  // Cross-origin when VITE_API_URL points at Render; same-origin in local dev.
  credentials: apiUrl ? "omit" : "same-origin",
});

const cache = new InMemoryCache({
  typePolicies: {
    Query: {
      fields: {
        search: {
          keyArgs: ["q", "limit"],
        },
      },
    },
    HopNode: { keyFields: false },
    HopLink: { keyFields: false },
    PagePerson: { keyFields: false },
    HopResponse: { keyFields: false },
    PeoplePage: { keyFields: false },
    SearchPersonHit: { keyFields: false },
    SearchOrgHit: { keyFields: false },
    SearchResults: { keyFields: false },
    GroupSummary: { keyFields: false },
  },
});

export const apolloClient = new ApolloClient({
  link: httpLink,
  cache,
  devtools: { enabled: import.meta.env.DEV },
});
