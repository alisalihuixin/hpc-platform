"""Shared column types.

``JSONDict`` renders as ``JSONB`` on PostgreSQL (indexable, matching the ERD)
and falls back to plain ``JSON`` on SQLite so the same metadata also builds in
the test suite.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

JSONDict = JSONB().with_variant(JSON(), "sqlite")
