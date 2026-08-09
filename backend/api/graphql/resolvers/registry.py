from __future__ import annotations

"""QueryType instance + per-request session helper.

Resolvers register themselves against ``query`` at import time; the package
``__init__`` imports every resolver module so all fields are bound before
``make_executable_schema`` runs.
"""

from typing import Any

from ariadne import QueryType
from sqlalchemy.orm import Session

query = QueryType()
def _session(info: Any) -> Session:
    return info.context["db"]
