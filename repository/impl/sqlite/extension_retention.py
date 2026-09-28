# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the package digests that the main database still refers to."""

from __future__ import annotations

from dataclasses import dataclass

from repository.impl.sqlite.connection import SqliteDatabase

# A runtime revision keeps every candidate that an operation ever reserved, so
# a copy that was once selected is never removed.
_RETAINED_SQL = """
SELECT digest FROM (
    SELECT package_digest AS digest FROM extension_packages
    UNION SELECT package_digest FROM extension_requests
    UNION SELECT json_extract(package.value, '$.extension_info.package_digest')
    FROM extension_runtime_revisions, json_each(extension_runtime_revisions.selection, '$.packages') AS package
) WHERE digest IS NOT NULL
"""


@dataclass(frozen=True)
class SqliteRetainedDigests:
    """Read the retained digests at one snapshot."""

    database: SqliteDatabase

    def retained_digests(self) -> frozenset[str]:
        """Read every referred package digest.

        Returns:
            The digests whose copies must stay.

        """
        with self.database.read() as connection:
            rows = connection.execute(_RETAINED_SQL).fetchall()
        return frozenset(str(row[0]) for row in rows)
