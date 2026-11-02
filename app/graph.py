"""Neo4j access layer.

Graph model
-----------
(:Student {id, name})-[:STRUGGLES_WITH {error_rate, attempts, successes, updated_at}]->(:Phoneme {id, label, ipa})
(:Student)-[:HAD_SESSION]->(:Session {id, passage, wcpm, accuracy, at})
"""
from __future__ import annotations

import uuid
from typing import Any

from neo4j import GraphDatabase

from .config import settings
from .phonemes import BY_ID, CATALOG


def next_error_rate(before: float, attempts: int, success: bool, base_alpha: float) -> float:
    """Running average early on, exponential moving average later.

    alpha = max(base_alpha, 1/(attempts+2)); this mirrors the Cypher in Graph.record_observation.
    """
    alpha = max(base_alpha, 1.0 / (attempts + 2))
    miss = 0.0 if success else 1.0
    return before * (1 - alpha) + alpha * miss


class Graph:
    def __init__(self) -> None:
        self._driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=5,
            max_transaction_retry_time=3,
        )

    def close(self) -> None:
        self._driver.close()

    def ping(self) -> bool:
        self._driver.verify_connectivity()
        return True

    def _run_write(self, query: str, **params) -> list[dict[str, Any]]:
        def work(tx):
            return [r.data() for r in tx.run(query, **params)]

        with self._driver.session(database=settings.neo4j_database) as s:
            return s.execute_write(work)

    def _run_read(self, query: str, **params) -> list[dict[str, Any]]:
        def work(tx):
            return [r.data() for r in tx.run(query, **params)]

        with self._driver.session(database=settings.neo4j_database) as s:
            return s.execute_read(work)

    # ------------------------------------------------------------------ setup
    def init_schema(self) -> None:
        self._run_write("CREATE CONSTRAINT student_id IF NOT EXISTS FOR (s:Student) REQUIRE s.id IS UNIQUE")
        self._run_write("CREATE CONSTRAINT phoneme_id IF NOT EXISTS FOR (p:Phoneme) REQUIRE p.id IS UNIQUE")
        self._run_write("CREATE CONSTRAINT session_id IF NOT EXISTS FOR (x:Session) REQUIRE x.id IS UNIQUE")
        self._run_write(
            """
            UNWIND $phonemes AS ph
            MERGE (p:Phoneme {id: ph.id})
            SET p.label = ph.label, p.ipa = ph.ipa
            """,
            phonemes=[{"id": p.id, "label": p.label, "ipa": p.ipa} for p in CATALOG],
        )

    # ------------------------------------------------------------------ students
    def ensure_student(self, student_id: str, name: str) -> dict:
        rows = self._run_write(
            """
            MERGE (s:Student {id: $id})
            ON CREATE SET s.name = $name, s.created_at = datetime()
            RETURN s.id AS id, s.name AS name
            """,
            id=student_id,
            name=name,
        )
        return rows[0]

    def student_exists(self, student_id: str) -> bool:
        return bool(self._run_read("MATCH (s:Student {id: $id}) RETURN s.id AS id", id=student_id))

    # ------------------------------------------------------------------ step 1: context retrieval
    def struggles(self, student_id: str, threshold: float) -> list[dict]:
        """(Student)-[STRUGGLES_WITH {error_rate: > threshold}]->(Phoneme)"""
        return self._run_read(
            """
            MATCH (s:Student {id: $sid})-[r:STRUGGLES_WITH]->(p:Phoneme)
            WHERE r.error_rate > $threshold
            RETURN p.id AS id, p.label AS label, r.error_rate AS error_rate, r.attempts AS attempts
            ORDER BY r.error_rate DESC
            """,
            sid=student_id,
            threshold=threshold,
        )

    def profile(self, student_id: str) -> list[dict]:
        return self._run_read(
            """
            MATCH (s:Student {id: $sid})-[r:STRUGGLES_WITH]->(p:Phoneme)
            RETURN p.id AS id, p.label AS label, p.ipa AS ipa,
                   r.error_rate AS error_rate, r.attempts AS attempts, r.successes AS successes
            ORDER BY r.error_rate DESC
            """,
            sid=student_id,
        )

    # ------------------------------------------------------------------ step 4: graph evolution
    def record_observation(self, student_id: str, phoneme_id: str, success: bool) -> dict:
        rows = self._run_write(
            """
            MATCH (s:Student {id: $sid})
            MERGE (p:Phoneme {id: $pid})
            ON CREATE SET p.label = $label, p.ipa = $ipa
            MERGE (s)-[r:STRUGGLES_WITH]->(p)
            ON CREATE SET r.error_rate = $prior, r.attempts = 0, r.successes = 0, r.created_at = datetime()
            WITH r, r.error_rate AS before, r.attempts AS n
            WITH r, before, n,
                 CASE WHEN 1.0 / (n + 2) > $alpha THEN 1.0 / (n + 2) ELSE $alpha END AS a
            SET r.error_rate = before * (1 - a) + a * $miss,
                r.attempts = n + 1,
                r.successes = r.successes + $hit,
                r.updated_at = datetime()
            RETURN before, r.error_rate AS after, r.attempts AS attempts
            """,
            sid=student_id,
            pid=phoneme_id,
            label=BY_ID[phoneme_id].label,
            ipa=BY_ID[phoneme_id].ipa,
            prior=settings.phoneme_prior,
            alpha=settings.error_rate_alpha,
            miss=0.0 if success else 1.0,
            hit=1 if success else 0,
        )
        if not rows:
            raise LookupError(f"Student '{student_id}' or phoneme '{phoneme_id}' not found")
        return rows[0]

    def seed(self, student_id: str, phoneme_id: str, error_rate: float) -> dict:
        rows = self._run_write(
            """
            MATCH (s:Student {id: $sid})
            MERGE (p:Phoneme {id: $pid})
            ON CREATE SET p.label = $label, p.ipa = $ipa
            MERGE (s)-[r:STRUGGLES_WITH]->(p)
            ON CREATE SET r.attempts = 0, r.successes = 0, r.created_at = datetime()
            SET r.error_rate = $rate, r.updated_at = datetime()
            RETURN p.id AS id, r.error_rate AS error_rate
            """,
            sid=student_id,
            pid=phoneme_id,
            label=BY_ID[phoneme_id].label,
            ipa=BY_ID[phoneme_id].ipa,
            rate=float(error_rate),
        )
        if not rows:
            raise LookupError(f"Student '{student_id}' or phoneme '{phoneme_id}' not found")
        return rows[0]

    def log_session(self, student_id: str, passage: str, wcpm: float, accuracy: float) -> None:
        self._run_write(
            """
            MATCH (s:Student {id: $sid})
            CREATE (s)-[:HAD_SESSION]->(:Session {id: $xid, passage: $passage, wcpm: $wcpm, accuracy: $acc, at: datetime()})
            """,
            sid=student_id,
            xid=uuid.uuid4().hex,
            passage=passage[:200],
            wcpm=float(wcpm),
            acc=float(accuracy),
        )

    def recent_sessions(self, student_id: str, limit: int = 10) -> list[dict]:
        return self._run_read(
            """
            MATCH (:Student {id: $sid})-[:HAD_SESSION]->(x:Session)
            RETURN x.passage AS passage, x.wcpm AS wcpm, x.accuracy AS accuracy, toString(x.at) AS at
            ORDER BY x.at DESC LIMIT $limit
            """,
            sid=student_id,
            limit=limit,
        )
