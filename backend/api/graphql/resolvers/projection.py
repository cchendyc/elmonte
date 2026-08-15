from __future__ import annotations

"""projection + personCoauthorTies field resolvers."""

import math
import threading
import time
from datetime import date
from typing import Any

from sqlalchemy import text

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.repositories.people import _retired_at
from api.repositories.projection import _active_projection_run, _coauthor_ties_on_map
from api.services.impact import _normalize_impacts, _raw_person_impact
from api.services.names import _full_name

# Projection responses are fully determined by (view, run, calendar date) and
# only change on an offline atlas rebuild — cache them in-process with a TTL
# instead of re-querying 800+ rows per request.
_PROJECTION_CACHE: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
_PROJECTION_TTL = 3600.0
_PROJECTION_CACHE_MAX = 16
_PROJECTION_CACHE_LOCK = threading.Lock()


def _cached_projection(key: tuple[str, int, str]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _PROJECTION_CACHE_LOCK:
        hit = _PROJECTION_CACHE.get(key)
        if hit is None:
            return None
        if now - hit[0] > _PROJECTION_TTL:
            _PROJECTION_CACHE.pop(key, None)
            return None
        return hit[1]


def _store_projection(key: tuple[str, int, str], payload: dict[str, Any]) -> None:
    with _PROJECTION_CACHE_LOCK:
        if len(_PROJECTION_CACHE) >= _PROJECTION_CACHE_MAX:
            # Drop expired entries first; clear only when every slot is live.
            now = time.monotonic()
            for existing_key in list(_PROJECTION_CACHE):
                if now - _PROJECTION_CACHE[existing_key][0] > _PROJECTION_TTL:
                    _PROJECTION_CACHE.pop(existing_key, None)
        if len(_PROJECTION_CACHE) >= _PROJECTION_CACHE_MAX:
            _PROJECTION_CACHE.clear()
        _PROJECTION_CACHE[key] = (time.monotonic(), payload)


def _empty_projection(view: str) -> dict[str, Any]:
    return {
        "runId": "",
        "algorithm": "",
        "view": view,
        "pointCount": 0,
        "points": [],
        "clusters": [],
        "edges": [],
    }


@query.field("projection")
def resolve_projection(_obj, info, view: str = "topic") -> dict[str, Any]:
    view = view if view in ("topic", "network") else "topic"
    session = _session(info)

    # Per-request memoization: a query with N aliases of `projection` must not
    # run the same heavy SQL N times.  The process-level TTL cache handles
    # cross-request reuse; this handles alias amplification inside one request.
    request_cache = info.context.setdefault("_projection_cache", {})
    if view in request_cache:
        return request_cache[view]

    active = _active_projection_run(session)
    if active is None:
        payload = _empty_projection(view)
        request_cache[view] = payload
        return payload

    cache_key = (view, int(active["id"]), date.today().isoformat())
    cached = _cached_projection(cache_key)
    if cached is not None:
        request_cache[view] = cached
        return cached

    rows = session.execute(
        text(
            """
            SELECT
              p.person_id,
              p.x,
              p.y,
              p.cluster_id,
              c.label           AS cluster_label,
              pe.firstname,
              pe.middlename,
              pe.lastname,
              pa.title,
              pa.position_rank,
              pa.organization_id AS anchor_org_id,
              pa.validity,
              pa.ends_at,
              inst.id            AS institution_id,
              inst.name          AS institution_name,
              coalesce(impact.publication_count, 0) AS publication_count,
              coalesce(impact.citation_count, 0)    AS citation_count,
              impact.last_publication_year
            FROM person_projections_2d p
            JOIN people pe ON pe.id = p.person_id
            LEFT JOIN projection_clusters c
              ON c.run_id = p.run_id AND c.view = p.view AND c.cluster_index = p.cluster_id
            LEFT JOIN LATERAL (
              SELECT
                pa.title,
                pa.position_rank,
                pa.organization_id,
                pa.validity,
                paf.ends_at
              FROM person_anchor pa
              JOIN person_affiliations paf ON paf.id = pa.affiliation_id
              WHERE pa.person_id = p.person_id
              ORDER BY
                CASE WHEN pa.validity @> CURRENT_DATE THEN 0 ELSE 1 END,
                pa.is_primary DESC,
                upper(pa.validity) DESC NULLS FIRST,
                paf.starts_at DESC NULLS LAST
              LIMIT 1
            ) pa ON TRUE
            LEFT JOIN LATERAL (
              SELECT o.id, o.name
              FROM org_tree_current t
              JOIN organizations o ON o.id = ANY(t.ancestor_ids)
              WHERE t.organization_id = pa.organization_id
                AND o.kind = 'university'
              ORDER BY array_position(t.ancestor_ids, o.id)
              LIMIT 1
            ) inst ON TRUE
            -- Aggregate publications once for the whole projection instead of
            -- a LATERAL subquery per person (817 row sets → 1 GROUP BY).
            LEFT JOIN (
              SELECT
                pa_pub.person_id,
                count(*)::int AS publication_count,
                coalesce(sum(pub.cited_by_count), 0)::int AS citation_count,
                max(pub.publication_year)::int AS last_publication_year
              FROM publication_authors pa_pub
              JOIN publications pub ON pub.id = pa_pub.publication_id
              WHERE pa_pub.person_id IN (
                SELECT pp.person_id FROM person_projections_2d pp
                WHERE pp.run_id = :run_id AND pp.view = :view
              )
              GROUP BY pa_pub.person_id
            ) impact ON impact.person_id = p.person_id
            WHERE p.run_id = :run_id AND p.view = :view
            """
        ),
        {"run_id": active["id"], "view": view},
    ).mappings().all()

    raw_impact: dict[str, float] = {}
    for r in rows:
        pid = encode("person", int(r["person_id"]))
        raw_impact[pid] = _raw_person_impact(
            int(r["citation_count"]),
            int(r["publication_count"]),
            r["position_rank"],
        )
    impact_by_id = _normalize_impacts(raw_impact)

    points = []
    for r in rows:
        if not math.isfinite(float(r["x"])) or not math.isfinite(float(r["y"])):
            # A malformed projection row must never turn the whole map into
            # NaN (JSONResponse rejects NaN) or a browser-side blank screen.
            continue
        anchor_row = {"validity": r.get("validity"), "ends_at": r.get("ends_at")}
        pid = encode("person", int(r["person_id"]))
        points.append(
            {
                "id": pid,
                "label": _full_name(r["firstname"], r["middlename"], r["lastname"]),
                "x": float(r["x"]),
                "y": float(r["y"]),
                "institution": r["institution_name"],
                "institutionId": (
                    encode("org", int(r["institution_id"]))
                    if r["institution_id"] is not None
                    else None
                ),
                "rank": r["position_rank"],
                "impact": impact_by_id[pid],
                "clusterId": int(r["cluster_id"]) if r["cluster_id"] is not None else None,
                "clusterLabel": r["cluster_label"],
                "retiredAt": _retired_at(anchor_row, date.today()),
                "lastPublicationYear": (
                    int(r["last_publication_year"])
                    if r.get("last_publication_year") is not None
                    else None
                ),
            }
        )

    clusters = [
        {
            "id": int(row["cluster_index"]),
            "label": row["label"],
            "fieldName": row["field_name"],
            "memberCount": int(row["member_count"]),
            "cx": float(row["cx"]),
            "cy": float(row["cy"]),
            "colorSlot": int(row["color_slot"]),
        }
        for row in session.execute(
            text(
                "SELECT cluster_index, label, field_name, member_count, cx, cy, color_slot "
                "FROM projection_clusters WHERE run_id = :run_id AND view = :view "
                "ORDER BY cluster_index"
            ),
            {"run_id": active["id"], "view": view},
        ).mappings().all()
    ]
    clusters = [c for c in clusters if math.isfinite(c["cx"]) and math.isfinite(c["cy"])]
    cluster_ids = {int(c["id"]) for c in clusters}
    points = [
        {
            **point,
            "clusterId": point["clusterId"]
            if point["clusterId"] in cluster_ids
            else None,
            "clusterLabel": point["clusterLabel"]
            if point["clusterId"] in cluster_ids
            else None,
        }
        for point in points
    ]
    edges = [
        {
            "sourceCluster": int(row["source_cluster"]),
            "targetCluster": int(row["target_cluster"]),
            "collaborationWeight": row["collaboration_weight"],
            "topicWeight": row["topic_weight"],
        }
        for row in session.execute(
            text(
                "SELECT source_cluster, target_cluster, collaboration_weight, topic_weight "
                "FROM projection_cluster_edges WHERE run_id = :run_id AND view = :view"
            ),
            {"run_id": active["id"], "view": view},
        ).mappings().all()
    ]
    edges = [
        edge
        for edge in edges
        if edge["sourceCluster"] in cluster_ids
        and edge["targetCluster"] in cluster_ids
        and (
            edge["collaborationWeight"] is None
            or (
                math.isfinite(edge["collaborationWeight"])
                and edge["collaborationWeight"] >= 0
            )
        )
        and (
            edge["topicWeight"] is None
            or (math.isfinite(edge["topicWeight"]) and edge["topicWeight"] >= 0)
        )
    ]
    payload = {
        "runId": str(active["id"]),
        "algorithm": active["algorithm"],
        "view": view,
        "pointCount": len(points),
        "points": points,
        "clusters": clusters,
        "edges": edges,
    }
    _store_projection(cache_key, payload)
    request_cache[view] = payload
    return payload


@query.field("personCoauthorTies")
def resolve_person_coauthor_ties(_obj, info, personId: str, view: str = "topic") -> list[dict[str, Any]]:
    row_id = _decode_id(personId, "person")
    session = _session(info)
    active = _active_projection_run(session)
    if active is None:
        return []
    view = view if view in ("topic", "network") else "topic"
    rows = _coauthor_ties_on_map(session, row_id, int(active["id"]), view)
    return [
        {
            "personId": encode("person", int(r["other_id"])),
            "paperCount": int(r["paper_count"]),
        }
        for r in rows
    ]
