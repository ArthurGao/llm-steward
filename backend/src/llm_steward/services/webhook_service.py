"""Webhook delivery (design §7): the outbox worker sends one queued event to one endpoint.

The request body is JSON `{id, type, created_at, namespace_id, data}`; `data` has ids and states
only. Headers: `Steward-Event-Id`, `Steward-Event`, and `Steward-Signature: t=<unix seconds>,
v1=<hex HMAC-SHA256 of "<t>." + body>` with the secret from the environment variable the
namespace config names (never stored). The receiver checks the signature and the timestamp
(reject old ones), and dedupes on the event id: delivery is at least once.

2xx is delivered. 408, 429, 5xx and network errors back off and retry. Any other answer (a 3xx
included: redirects are not followed), a missing secret or a URL the guard refuses fails the job
at once (`steward jobs failed`). An endpoint removed from the config meanwhile is not called.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward import outbound
from llm_steward.errors import TooLarge
from llm_steward.ids import uuid7
from llm_steward.models import Candidate, KbEntry
from llm_steward.models.base import utcnow
from llm_steward.models.enums import JobType
from llm_steward.namespace_config import WebhookEvent, read_config
from llm_steward.outbound import Outbound
from llm_steward.services.job_queue import ClaimedJob, PermanentFailure, TryLater, enqueue

MAX_ANSWER_BYTES = 64 * 1024  # what is read of the receiver's answer


async def queue_event(
    s: AsyncSession, namespace_id: uuid.UUID, event: WebhookEvent, data: dict[str, Any]
) -> None:
    """One webhook delivery per endpoint subscribed to `event` (design §7), queued in the
    caller's transaction: sent only if the write commits. `data` holds ids and states only, never
    content (the host fetches what it may see through the API)."""
    hooks = [h for h in (await read_config(s, namespace_id)).webhooks if event in h.events]
    if not hooks:
        return
    event_id, at = str(uuid7()), utcnow().isoformat()
    for hook in hooks:
        payload = {"event_id": event_id, "event": event, "url": hook.url, "at": at, "data": data}
        enqueue(s, namespace_id, JobType.WEBHOOK_DELIVER, payload)


# Event data: ids and states, never content.


def candidate_event(candidate: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": str(candidate.id),
        "stage": candidate.stage.value,
        "target_type": candidate.target_type.value,
        "op": candidate.op.value,
        "risk_level": candidate.risk_level.value,
        "user_id": candidate.requested_for_user,  # whom to show the card to
    }


def entry_event(entry: KbEntry) -> dict[str, Any]:
    return {
        "entry_id": str(entry.id),
        "collection_id": str(entry.collection_id),
        "status": entry.status.value,
        "version": entry.version,
    }


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


class WebhookService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], out: Outbound) -> None:
        self._sessions = sessions
        self._outbound = out

    async def _exists(self, candidate_id: str) -> bool:
        async with self._sessions() as s:
            return await s.get(Candidate, uuid.UUID(candidate_id)) is not None

    async def handle(self, job: ClaimedJob) -> None:
        p = job.payload
        async with self._sessions() as s:
            config = await read_config(s, job.namespace_id)
        hook = next((h for h in config.webhooks if h.url == p["url"]), None)
        if hook is None or p["event"] not in hook.events:
            return  # unsubscribed since: dropped, not failed
        if p["event"] == "candidate.created" and not await self._exists(p["data"]["candidate_id"]):
            return  # the card is gone (answered, expired, or its user erased): nothing to show
        secret = os.environ.get(hook.secret_env)
        if not secret:
            raise PermanentFailure(f"webhook secret {hook.secret_env} is not set")
        body = json.dumps(
            {
                "id": p["event_id"],
                "type": p["event"],
                "created_at": p["at"],
                "namespace_id": str(job.namespace_id),
                "data": p["data"],
            },
            separators=(",", ":"),
        ).encode()
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "llm-steward-webhooks",
            "Steward-Event-Id": p["event_id"],
            "Steward-Event": p["event"],
            "Steward-Signature": f"t={timestamp},v1={sign(secret, timestamp, body)}",
        }
        try:
            answer = await self._outbound.request(
                "POST", hook.url, content=body, headers=headers, max_bytes=MAX_ANSWER_BYTES
            )
        except outbound.UrlNotAllowed as exc:
            raise PermanentFailure(f"webhook URL refused: {exc.detail}") from exc
        except (httpx2.TransportError, TooLarge) as exc:
            raise TryLater(f"webhook delivery failed: {type(exc).__name__}") from exc
        status = answer.status_code
        if 200 <= status < 300:
            return
        if outbound.retryable(status):
            raise TryLater(f"the receiver answered {status}")
        raise PermanentFailure(f"the receiver answered {status}; not retried")
