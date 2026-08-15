"""SQLAlchemy mirror of db/schema.sql.

schema.sql is authoritative. These models exist for typed access and are kept
in sync with it.

No `relationship()`
-------------------
Models declare foreign key *columns* only. Traverse with an explicit join:

    select(Person.firstname, Person.lastname, Award.name)
        .join(PersonAward, PersonAward.person_id == Person.id)
        .join(Award, Award.id == PersonAward.award_id)

rather than `person.awards`. Relationships resolve their targets from
SQLAlchemy's class registry at `configure_mappers()` time, not from Python
imports, so a mistyped target or a mismatched `back_populates` fails at runtime
far from the definition and is invisible to type checkers. Explicit joins also
avoid lazy-load N+1s and `DetachedInstanceError`, and matter more once queries
run under async.

Referential integrity is enforced in application code, not with foreign keys.
Internal row ids are BIGINT; TEXT is reserved for actual strings (names, URLs,
external ids in `external_identifiers`).

Not expressed here, having no declarative form:

* the `set_updated_at()` trigger that maintains `updated_at`
* the materialized views and their indexes:
    - `org_tree_current` — as-of-today org tree with ancestor paths and rollups
    - `person_anchor` — chart-anchored affiliations, resolved to a person's
      current tree slot at query time via a validity filter
    - `person_coauthor_edges` — canonical undirected coauthor pair-weights
    - `org_current_roster` — chart-anchored people per org with a stable sort
      key for keyset pagination

Enum types are declared with `create_type=False`, so schema.sql owns their
lifecycle and SQLAlchemy only references them.
"""

from db.models import enums
from db.models.affiliation import AffiliationOrgAssignment, PersonAffiliation
from db.models.award import Award, PersonAward
from db.models.base import Base
from db.models.concept import Concept, PersonConcept, PublicationConcept
from db.models.grant import Grant, GrantParticipant
from db.models.organization import Organization, OrgRelationship
from db.models.person import Person, PersonAlias
from db.models.person_relationship import PersonRelationship
from db.models.projection import (
    EmbeddingRun,
    PersonProjection2D,
    ProjectionCluster,
    ProjectionClusterEdge,
)
from db.models.provenance import Evidence, ExternalIdentifier, SourceSnapshot
from db.models.publication import (
    Publication,
    PublicationAuthor,
    PublicationAuthorAffiliation,
    PublicationCitation,
)
from db.models.topic import PersonTopic, PublicationTopic, Topic

__all__ = [
    "AffiliationOrgAssignment",
    "Award",
    "Base",
    "Concept",
    "EmbeddingRun",
    "Evidence",
    "ExternalIdentifier",
    "Grant",
    "GrantParticipant",
    "OrgRelationship",
    "Organization",
    "Person",
    "PersonAffiliation",
    "PersonAlias",
    "PersonAward",
    "PersonConcept",
    "PersonProjection2D",
    "PersonRelationship",
    "PersonTopic",
    "ProjectionCluster",
    "ProjectionClusterEdge",
    "Publication",
    "PublicationAuthor",
    "PublicationAuthorAffiliation",
    "PublicationCitation",
    "PublicationConcept",
    "PublicationTopic",
    "SourceSnapshot",
    "Topic",
    "enums",
]
