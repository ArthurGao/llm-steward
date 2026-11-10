"""`Idempotency-Key` on writes (design §7; semantics of the IETF httpapi idempotency draft).

A key belongs to its caller (namespace + API key + the token's user) and is kept 24 hours. The
first request reserves it; a retry with the same key and the same request replays the stored
response; the same key for a different request is refused (422); a retry while the first is still
running is a conflict (409). A server error (5xx) releases the key, so the retry runs for real;
any other answer, a 4xx included, is the answer to that request and is replayed.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, Unprocessable
from llm_steward.models import IdempotencyKey
from llm_steward.models.base import utcnow
from llm_steward.services.namespace_service import Principal

KEEP = timedelta(hours=24)


class IdempotencyKeyReused(Unprocessable):
    code = "idempotency_key_reused"


class IdempotencyInProgress(Conflict):
    code = "idempotency_in_progress"


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    content_type: str | None
    body: bytes


def principal_ref(principal: Principal) -> str:
    """Whose key space: the API key, and under a user token that token's user."""
    ref = f"api_key:{principal.key_id}"
    return ref if principal.user_id is None else f"{ref}/user:{principal.user_id}"


def request_hash(method: str, path: str, query: str, content_type: str, body: bytes) -> str:
    """The exact request a key was first used for."""
    digest = hashlib.sha256()
    for part in (method, path, query, content_type):
        digest.update(part.encode() + b"\0")
    digest.update(body)
    return digest.hexdigest()


def _cutoff() -> datetime:
    return utcnow() - KEEP


class IdempotencyService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def reserve(
        self, namespace_id: uuid.UUID, principal: str, key: str, digest: str
    ) -> uuid.UUID | StoredResponse:
        """A reservation to run the request under (its id), or the response to replay."""
        try:
            async with self._sessions() as s, s.begin():
                row = await s.scalar(
                    select(IdempotencyKey)
                    .where(
                        IdempotencyKey.namespace_id == namespace_id,
                        IdempotencyKey.principal == principal,
                        IdempotencyKey.key == key,
                    )
                    .with_for_update()
                )
                if row is None:
                    row = IdempotencyKey(
                        namespace_id=namespace_id, principal=principal, key=key, request_hash=""
                    )
                    s.add(row)
                elif row.created_at >= _cutoff():
                    return _existing(row, digest)
                # new, or expired (forgotten, as if never sent): reserve it for this request
                row.request_hash, row.created_at = digest, utcnow()
                row.status_code, row.content_type, row.body = None, None, None
                await s.flush()
                return row.id
        except IntegrityError as exc:  # a concurrent first request reserved it a moment ago
            raise IdempotencyInProgress(_IN_PROGRESS) from exc

    async def complete(
        self, reservation: uuid.UUID, status_code: int, content_type: str | None, body: bytes
    ) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(
                update(IdempotencyKey)
                .where(IdempotencyKey.id == reservation)
                .values(status_code=status_code, content_type=content_type, body=body)
            )

    async def release(self, reservation: uuid.UUID) -> None:
        async with self._sessions() as s, s.begin():
            await s.execute(delete(IdempotencyKey).where(IdempotencyKey.id == reservation))

    async def purge(self) -> int:
        """Forget keys older than a day (a worker tick)."""
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                delete(IdempotencyKey)
                .where(IdempotencyKey.created_at < _cutoff())
                .returning(IdempotencyKey.id)
            )
            return len(result.all())


_IN_PROGRESS = "a request with this Idempotency-Key is still being processed; retry later"


def _existing(row: IdempotencyKey, digest: str) -> StoredResponse:
    if row.request_hash != digest:
        raise IdempotencyKeyReused("this Idempotency-Key was already used for a different request")
    if row.status_code is None:
        raise IdempotencyInProgress(_IN_PROGRESS)
    return StoredResponse(row.status_code, row.content_type, row.body or b"")
