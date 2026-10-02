import time
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Float,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)


class Store:
    """Versioned metadata only. No SMTP/DB credentials or HTML retained here."""

    def __init__(self, url: str):
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False}
            if url.startswith("sqlite")
            else {},
        )
        metadata = MetaData()
        self.records = Table(
            "lab_records",
            metadata,
            Column("id", String(200), primary_key=True),
            Column("kind", String(32), nullable=False, index=True),
            Column("data", JSON, nullable=False),
            Column("created", Float, nullable=False),
        )
        metadata.create_all(self.engine)
        current = self.get("schema", "version")
        if current and current["version"] != 1:
            raise ValueError("unsupported_database_schema")
        self.put("schema", "version", {"version": 1})

    def put(self, kind: str, identifier: str, data: dict[str, Any]) -> None:
        key = f"{kind}:{identifier}"
        with self.engine.begin() as conn:
            if conn.execute(
                select(self.records.c.id).where(self.records.c.id == key)
            ).first():
                conn.execute(
                    update(self.records)
                    .where(self.records.c.id == key)
                    .values(data=data)
                )
            else:
                conn.execute(
                    insert(self.records).values(
                        id=key, kind=kind, data=data, created=time.time()
                    )
                )

    def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            value = conn.execute(
                select(self.records.c.data).where(
                    self.records.c.id == f"{kind}:{identifier}"
                )
            ).scalar()
            return dict(value) if value else None

    def list(self, kind: str, limit: int = 300) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [
                dict(row[0])
                for row in conn.execute(
                    select(self.records.c.data)
                    .where(self.records.c.kind == kind)
                    .order_by(self.records.c.created.desc())
                    .limit(limit)
                )
            ]

    def remove(self, kind: str, identifier: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                delete(self.records).where(self.records.c.id == f"{kind}:{identifier}")
            )

    def event_counts(self) -> dict[str, int]:
        """Count retained events by type without truncating the live event feed."""
        event_type = self.records.c.data["type"].as_string()
        with self.engine.connect() as conn:
            return {
                str(kind): int(count)
                for kind, count in conn.execute(
                    select(event_type, func.count())
                    .where(self.records.c.kind == "event")
                    .group_by(event_type)
                )
                if kind
            }

    def event_count_for_alerts(self, event_kind: str, alert_ids: set[str]) -> int:
        """Count one event type only for alerts still visible in the DBA inbox."""
        if not alert_ids:
            return 0
        event_type = self.records.c.data["type"].as_string()
        alert_id = self.records.c.data["payload"]["alert_id"].as_string()
        with self.engine.connect() as conn:
            return int(
                conn.execute(
                    select(func.count())
                    .where(self.records.c.kind == "event")
                    .where(event_type == event_kind)
                    .where(alert_id.in_(alert_ids))
                ).scalar_one()
            )

    def prune(self, days: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                delete(self.records).where(
                    self.records.c.kind.in_(["event", "approval"]),
                    self.records.c.created < time.time() - days * 86400,
                )
            )
