"""Declarative base and portable column types (PostgreSQL and SQLite)."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from llm_steward.ids import uuid7

# JSONB on PostgreSQL, JSON (text) on SQLite.
JsonType = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC in and out. SQLite drops the offset, so it is re-attached on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware datetime (UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Tables owned by a provider (the native index), not by the governance migrations.
PROVIDER_OWNED_PREFIX = "native_"


def include_in_migrations(name: str | None, type_: str, parent_names: object) -> bool:
    """Alembic `include_name` hook: autogenerate and drift checks ignore provider-owned tables."""
    return not (type_ == "table" and name is not None and name.startswith(PROVIDER_OWNED_PREFIX))


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_column(enum: type[StrEnum], name: str) -> Enum:
    """VARCHAR + CHECK on both dialects storing the enum *values*; the StrEnum is the source."""
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    type_annotation_map: dict[Any, Any] = {  # noqa: RUF012 - SQLAlchemy reads this mapping
        uuid.UUID: Uuid(),
        datetime: UtcDateTime(),
        dict[str, Any]: JsonType,
        list[str]: JsonType,
        list[dict[str, Any]]: JsonType,
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TimestampMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
