"""Seeded-data ingestion for PulseLink.

The :mod:`pulselink.ingest.importer` module reads ``Dataset.csv`` from the local
filesystem (mirroring the production S3 import mapping; a simple local/Lambda
importer — no AWS Glue) and maps it onto the shared domain models, then
persists the result to local PostgreSQL scoped by ``city_id``.
"""

from pulselink.ingest.importer import (
    ImportCounts,
    MappingResult,
    import_dataset,
    map_dataset,
    read_csv_rows,
)

__all__ = [
    "ImportCounts",
    "MappingResult",
    "import_dataset",
    "map_dataset",
    "read_csv_rows",
]
