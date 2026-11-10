"""Requeueing jobs that ran out of attempts (design §11 P2 follow-up: a failed extraction leaves
its episodes marked processed, so the job itself must be retried), both dialects."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import select, update
from typer.testing import CliRunner

from llm_steward.models import Job, Memory
from llm_steward.models.enums import JobStatus
from llm_steward.services.job_queue import JobQueue
from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM
from tests.fakes.memory_provider import FakeMemoryProvider


@pytest.fixture
def client(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url, memory_provider=FakeMemoryProvider(), llm=FakeLLM()) as c:
        yield c


def _queue(c: AppClient) -> JobQueue:
    return JobQueue(c.sessions)


def _buffered_extraction(c: AppClient, h: dict[str, str], text: str) -> uuid.UUID:
    body = {"messages": [{"role": "user", "content": text}], "user_id": "u1"}
    assert c.http.post("/v1/episodes", json=body, headers=h).status_code == 202
    r = c.http.post("/v1/episodes/flush", json={"user_id": "u1"}, headers=h)
    return uuid.UUID(r.json()["job_id"])


def _set_status(c: AppClient, job_id: uuid.UUID, status: JobStatus) -> None:
    async def write() -> None:
        async with c.sessions() as s, s.begin():
            values = {"status": status, "attempts": 8, "error": "RuntimeError: engine down"}
            await s.execute(update(Job).where(Job.id == job_id).values(**values))

    c.call(write)


def _contents(c: AppClient) -> list[str]:
    async def read() -> list[str]:
        async with c.sessions() as s:
            return list(await s.scalars(select(Memory.content)))

    return c.call(read)


def test_a_failed_extraction_runs_again_after_retry(client: AppClient) -> None:
    h = client.new_key("acme")
    job_id = _buffered_extraction(client, h, "我喜欢绿茶")
    _set_status(client, job_id, JobStatus.FAILED)  # ran out of attempts
    client.drain()
    assert _contents(client) == []  # a failed job is never claimed again by itself
    [failed] = client.call(_queue(client).failed)
    assert (failed.id, failed.error) == (job_id, "RuntimeError: engine down")

    assert client.call(_queue(client).retry, [job_id]) == 1
    retried = client.get(Job, job_id)
    assert (retried.status, retried.attempts) == (JobStatus.PENDING, 0)
    client.drain()
    assert _contents(client) == ["我喜欢绿茶"]
    assert client.get(Job, job_id).status == JobStatus.DONE
    assert client.call(_queue(client).failed) == []


def test_only_failed_jobs_are_requeued(client: AppClient) -> None:
    h = client.new_key("acme")
    done = _buffered_extraction(client, h, "我住北京")
    client.drain()
    failed = _buffered_extraction(client, h, "我住上海")
    _set_status(client, failed, JobStatus.FAILED)
    assert client.call(_queue(client).retry, [done]) == 0
    assert client.get(Job, done).status == JobStatus.DONE
    assert client.call(_queue(client).retry, None) == 1  # every failed job, and only those
    assert client.get(Job, failed).status == JobStatus.PENDING


def test_the_cli_lists_and_retries(cli_db: str) -> None:
    from llm_steward.cli import app

    runner = CliRunner()
    assert runner.invoke(app, ["ns", "create", "acme"]).exit_code == 0
    with running_app(cli_db, memory_provider=FakeMemoryProvider(), llm=FakeLLM()) as c:
        job_id = _buffered_extraction(c, c.new_key("other"), "我喜欢绿茶")
        _set_status(c, job_id, JobStatus.FAILED)
    listed = runner.invoke(app, ["jobs", "failed"])
    assert listed.exit_code == 0 and str(job_id) in listed.output
    assert "engine down" in listed.output
    assert runner.invoke(app, ["jobs", "failed", "--ns", "acme"]).output == ""  # not acme's
    assert runner.invoke(app, ["jobs", "retry"]).exit_code == 1  # ids or --all-failed
    assert runner.invoke(app, ["jobs", "retry", str(job_id), "--all-failed"]).exit_code == 1
    result = runner.invoke(app, ["jobs", "retry", str(job_id)])
    assert (result.exit_code, result.output) == (0, "requeued 1 job(s)\n")
    assert runner.invoke(app, ["jobs", "failed"]).output == ""
