"""HTTP API — thin FastAPI layer over db/models.

Keeps the graph exploration endpoints (`/graph/expand/{id}`, `/graph/pages`)
and search (`/search`) served from the same materialized views the frontend's
one-hop model was designed around.
"""
