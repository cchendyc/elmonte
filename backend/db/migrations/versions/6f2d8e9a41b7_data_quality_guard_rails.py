"""Data-quality guard rails for projection and provenance tables.

Revision ID: 6f2d8e9a41b7
Revises: d0e1f2a3b4c5
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "6f2d8e9a41b7"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A short-name trigram index: org search tests short_name as well as name,
    # and at scale that predicate otherwise scans every organization row.
    op.create_index(
        "idx_organizations_short_name_trgm",
        "organizations",
        ["short_name"],
        postgresql_using="gin",
        postgresql_ops={"short_name": "gin_trgm_ops"},
    )

    # Values rendered as links must be link-shaped at the source too. The
    # frontend re-validates (defense in depth), but a bad URL should not be
    # storable in the first place.
    op.create_check_constraint(
        "organizations_homepage_url_scheme",
        "organizations",
        "homepage_url IS NULL OR homepage_url ~* '^https?://'",
    )
    op.create_check_constraint(
        "source_snapshots_source_url_scheme",
        "source_snapshots",
        "source_url ~* '^https?://'",
    )
    op.create_check_constraint(
        "source_snapshots_http_status_range",
        "source_snapshots",
        "http_status IS NULL OR http_status BETWEEN 100 AND 599",
    )
    op.create_check_constraint(
        "external_identifiers_external_id_not_blank",
        "external_identifiers",
        "btrim(external_id) <> ''",
    )

    # Projection tables feed the home canvas. Guard the arithmetic the UI
    # performs on them: NaN/Infinity coordinates would otherwise surface as a
    # JSON serialization error, and negative cluster weights would render as
    # invalid CSS/SVG values.
    op.create_check_constraint(
        "embedding_runs_point_count_non_negative",
        "embedding_runs",
        "point_count >= 0",
    )
    op.create_check_constraint(
        "person_projections_2d_view_allowed",
        "person_projections_2d",
        "view IN ('topic', 'network')",
    )
    op.create_check_constraint(
        "person_projections_2d_xy_bounded",
        "person_projections_2d",
        "x BETWEEN -1000000 AND 1000000 AND y BETWEEN -1000000 AND 1000000",
    )
    op.create_check_constraint(
        "person_projections_2d_cluster_id_non_negative",
        "person_projections_2d",
        "cluster_id IS NULL OR cluster_id >= 0",
    )
    op.create_check_constraint(
        "projection_clusters_view_allowed",
        "projection_clusters",
        "view IN ('topic', 'network')",
    )
    op.create_check_constraint(
        "projection_clusters_member_count_positive",
        "projection_clusters",
        "member_count > 0",
    )
    op.create_check_constraint(
        "projection_clusters_xy_bounded",
        "projection_clusters",
        "cx BETWEEN -1000000 AND 1000000 AND cy BETWEEN -1000000 AND 1000000",
    )
    op.create_check_constraint(
        "projection_clusters_color_slot_non_negative",
        "projection_clusters",
        "color_slot >= 0",
    )
    op.create_check_constraint(
        "projection_cluster_edges_view_allowed",
        "projection_cluster_edges",
        "view IN ('topic', 'network')",
    )
    op.create_check_constraint(
        "projection_cluster_edges_no_self",
        "projection_cluster_edges",
        "source_cluster <> target_cluster",
    )
    op.create_check_constraint(
        "projection_cluster_edges_weights_non_negative",
        "projection_cluster_edges",
        "(collaboration_weight IS NULL OR collaboration_weight >= 0) AND "
        "(topic_weight IS NULL OR topic_weight >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "projection_cluster_edges_weights_non_negative",
        "projection_cluster_edges",
        type_="check",
    )
    op.drop_constraint(
        "projection_cluster_edges_no_self",
        "projection_cluster_edges",
        type_="check",
    )
    op.drop_constraint(
        "projection_cluster_edges_view_allowed",
        "projection_cluster_edges",
        type_="check",
    )
    op.drop_constraint(
        "projection_clusters_color_slot_non_negative",
        "projection_clusters",
        type_="check",
    )
    op.drop_constraint(
        "projection_clusters_xy_bounded",
        "projection_clusters",
        type_="check",
    )
    op.drop_constraint(
        "projection_clusters_member_count_positive",
        "projection_clusters",
        type_="check",
    )
    op.drop_constraint(
        "projection_clusters_view_allowed",
        "projection_clusters",
        type_="check",
    )
    op.drop_constraint(
        "person_projections_2d_cluster_id_non_negative",
        "person_projections_2d",
        type_="check",
    )
    op.drop_constraint(
        "person_projections_2d_xy_bounded",
        "person_projections_2d",
        type_="check",
    )
    op.drop_constraint(
        "person_projections_2d_view_allowed",
        "person_projections_2d",
        type_="check",
    )
    op.drop_constraint(
        "embedding_runs_point_count_non_negative",
        "embedding_runs",
        type_="check",
    )
    op.drop_constraint(
        "external_identifiers_external_id_not_blank",
        "external_identifiers",
        type_="check",
    )
    op.drop_constraint(
        "source_snapshots_http_status_range",
        "source_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "source_snapshots_source_url_scheme",
        "source_snapshots",
        type_="check",
    )
    op.drop_constraint(
        "organizations_homepage_url_scheme",
        "organizations",
        type_="check",
    )
    op.drop_index("idx_organizations_short_name_trgm", table_name="organizations")
