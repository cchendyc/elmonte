"""Postgres enum types.

These mirror the `CREATE TYPE` statements in schema.sql. `create_type=False`
means SQLAlchemy references the types without trying to create or drop them —
schema.sql owns their lifecycle.

Enum values order by declaration, not alphabetically, so `ORDER BY` on one of
these columns follows the order written here.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import ENUM

VERIFICATION_STATUS_VALUES = ("verified", "unverified", "disputed")
CLAIMED_STATUS_VALUES = ("unclaimed", "pending", "verified")
ORG_KIND_VALUES = (
    "university",
    "school",
    "department",
    "lab",
    "institute",
    "company",
    "funder",
    "nonprofit",
    "government",
    "consortium",
    "publisher",
)
ORG_RELATIONSHIP_TYPE_VALUES = ("primary", "secondary")
AFFILIATION_KIND_VALUES = (
    "employment",
    "education",
    "visiting",
    "founding",
    "governance",
    "honorary",
)
POSITION_RANK_VALUES = (
    "undergraduate",
    "masters_student",
    "phd_student",
    "visiting_student",
    "postdoc",
    "research_fellow",
    "technician",
    "engineer",
    "research_scientist",
    "staff_scientist",
    "lecturer",
    "adjunct_professor",
    "assistant_professor",
    "associate_professor",
    "full_professor",
    "emeritus_professor",
    "group_leader",
    "principal_investigator",
    "department_head",
    "dean",
    "executive",
    "board_member",
)
ASSIGNMENT_TYPE_VALUES = ("chart_anchor", "secondary")
PERSON_RELATIONSHIP_TYPE_VALUES = ("advised_by", "collaborated_with")
GRANT_ROLE_VALUES = ("principal_investigator", "co_investigator", "recipient")
IDENTIFIER_PROVIDER_VALUES = (
    "openalex",
    "orcid",
    "doi",
    "ror",
    "wikidata",
    "grid",
    "crossref_funder",
    "official_url",
)
SOURCE_KIND_VALUES = (
    "official_roster",
    "official_profile",
    "openalex",
    "crossref",
    "ror",
    "news",
    "manual",
)

verification_status = ENUM(
    *VERIFICATION_STATUS_VALUES, name="verification_status", create_type=False
)
claimed_status = ENUM(*CLAIMED_STATUS_VALUES, name="claimed_status", create_type=False)
org_kind = ENUM(*ORG_KIND_VALUES, name="org_kind", create_type=False)
org_relationship_type = ENUM(
    *ORG_RELATIONSHIP_TYPE_VALUES, name="org_relationship_type", create_type=False
)
affiliation_kind = ENUM(
    *AFFILIATION_KIND_VALUES, name="affiliation_kind", create_type=False
)
position_rank = ENUM(*POSITION_RANK_VALUES, name="position_rank", create_type=False)
assignment_type = ENUM(
    *ASSIGNMENT_TYPE_VALUES, name="assignment_type", create_type=False
)
person_relationship_type = ENUM(
    *PERSON_RELATIONSHIP_TYPE_VALUES, name="person_relationship_type", create_type=False
)
grant_role = ENUM(*GRANT_ROLE_VALUES, name="grant_role", create_type=False)
identifier_provider = ENUM(
    *IDENTIFIER_PROVIDER_VALUES, name="identifier_provider", create_type=False
)
source_kind = ENUM(*SOURCE_KIND_VALUES, name="source_kind", create_type=False)
