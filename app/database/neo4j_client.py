"""Thin wrapper around the official Neo4j Python driver."""

from typing import Any

from neo4j import GraphDatabase, ManagedTransaction
from neo4j.exceptions import DriverError, Neo4jError


class Neo4jClient:
    """Holds a single Neo4j driver and exposes small helper methods."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        """Create the driver. No connection is opened until it is first needed."""
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        """Close the driver and release all of its connections."""
        self._driver.close()

    def is_alive(self) -> bool:
        """Return True if the Neo4j server can be reached, False otherwise."""
        try:
            self._driver.verify_connectivity()
        except (DriverError, Neo4jError):
            return False
        return True

    def run_read(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read-only Cypher query and return every row as a dictionary."""

        def _work(tx: ManagedTransaction) -> list[dict[str, Any]]:
            """Execute the query inside a managed read transaction."""
            result = tx.run(query, params or {})
            return [record.data() for record in result]

        with self._driver.session() as session:
            return session.execute_read(_work)
