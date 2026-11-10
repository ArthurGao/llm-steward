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

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward import outbound
from llm_steward.errors import TooLarge
from llm_steward.services.governance_service import namespace_config
from llm_steward.services.job_queue import ClaimedJob, PermanentFailure

MAX_ANSWER_BYTES = 64 * 1024  # what is read of the receiver's answer


def sign(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


class WebhookService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, allow_private: bool) -> None:
        self._sessions = sessions
        self._allow_private = allow_private
        # The network boundary, replaceable (tests put a fake receiver and resolver here).
        self.transport: httpx2.AsyncBaseTransport | None = None
        self.resolve: outbound.Resolver = outbound.system_resolve

    async def handle(self, job: ClaimedJob) -> None:
        p = job.payload
        async with self._sessions() as s:
            config = await namespace_config(s, job.namespace_id)
        hook = next((h for h in config.webhooks if h.url == p["url"]), None)
        if hook is None or p["event"] not in hook.events:
            return  # unsubscribed since: dropped, not failed
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
            async with outbound.client(self.transport) as http:
                answer = await outbound.request(
                    http,
                    "POST",
                    hook.url,
                    content=body,
                    headers=headers,
                    max_bytes=MAX_ANSWER_BYTES,
                    allow_private=self._allow_private,
                    resolve=self.resolve,
                )
        except outbound.UrlNotAllowed as exc:
            raise PermanentFailure(f"webhook URL refused: {exc.detail}") from exc
        except (httpx2.TransportError, TooLarge) as exc:
            raise outbound.TryLater(f"webhook delivery failed: {type(exc).__name__}") from exc
        status = answer.status_code
        if 200 <= status < 300:
            return
        if outbound.retryable(status):
            raise outbound.TryLater(f"the receiver answered {status}")
        raise PermanentFailure(f"the receiver answered {status}; not retried")
