"""llm-steward's joint retrieval as a lite-eval target (design §11 P8). Tests only.

The labelled set (tests/eval/corpus.py, SYNTHETIC) is loaded once into a tenant of a running app
(`load`); a target answers each query with `POST /v1/retrieve` on that tenant, the hits mapped
back to the corpus keys, best first. lite-eval scores them with `recall@8` (relevance mode: the
share of a query's keys among the first 8 distinct ids) and gates the run without rerank at the
threshold the original test used; the run with rerank is reported only, as before.

A reply with anything in `degraded` (a provider timed out) is `invalid_output`: it counts as a
failed case, never as a quieter recall (the original test failed outright).
"""

import asyncio
import json
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from lite_eval.contracts import Case, Output
from lite_eval.datasets.local import load_dataset
from lite_eval.errors import ConfigError
from lite_eval.runner import RunOptions, RunResult, execute
from lite_eval.store import load_run
from lite_eval.suite import load_suite
from lite_eval.targets.base import TargetError

from tests.conftest import Tenant, post_memory
from tests.eval.corpus import DOCUMENTS, ENTRIES, MEMORIES, QUERIES

if TYPE_CHECKING:
    from lite_eval.datasets.local import Dataset

EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RERANKER = "BAAI/bge-reranker-v2-m3-int8"
TOP_K = 8
USER = "u1"
THRESHOLD = 0.92  # baseline 0.960 (rerank 0.980), 2026-11-09: one fully missed query of slack
METRIC = f"recall@{TOP_K}"
CONFIGS = {"plain": False, "rerank": True}  # name -> rerank; `plain` is the one the gate checks


def case_id(n: int) -> str:
    """The case id of the n-th query (from 1)."""
    return f"q{n:02d}"


def load(t: Tenant) -> dict[str, str]:
    """The corpus through the API; item id -> key."""
    keys: dict[str, str] = {}
    collection = t.collection(default_risk="low")
    for key, title, content in ENTRIES:
        r = t.submit(collection, content, title=title)
        assert r.status_code == 201, r.text
        keys[r.json()["entry"]["id"]] = key
    for key, name, text in DOCUMENTS:
        r = t.upload(collection, text.encode(), filename=name)
        assert r.status_code == 202, r.text
        keys[r.json()["document"]["id"]] = key
    for key, content in MEMORIES:
        keys[post_memory(t.c, t.host, content, user_id=USER)] = key
    t.c.drain()
    return keys


class StewardTarget:
    """One retrieval setting on a loaded tenant; see the module docstring."""

    name = "llm-steward"
    serial = True  # one app, one request at a time, as the original test

    def __init__(self, tenant: Tenant, keys: dict[str, str], *, db: str, rerank: bool) -> None:
        self.tenant = tenant
        self.keys = keys
        self.db = db
        self.rerank = rerank

    def config(self) -> dict[str, Any]:
        return {
            "db": self.db,
            "rerank": self.rerank,
            "top_k": TOP_K,
            "include_pinned": False,
            "embedding": EMBEDDING,
            **({"reranker": RERANKER} if self.rerank else {}),
        }

    def versions(self) -> dict[str, str]:
        return {"llm-steward": version("llm-steward")}

    def info(self) -> dict[str, str]:
        return {}

    def billing(self) -> Literal["free", "per_token", "unknown"]:
        return "free"  # local fastembed models only

    def setup_identity(self) -> dict[str, Any]:
        return {"db": self.db}  # every setting searches the one tenant the caller loaded

    async def setup(self, dataset: "Dataset") -> None:
        del dataset  # the tenant is loaded by the caller (`load`), once for every setting

    async def share_setup_from(self, leader: "StewardTarget") -> None:
        assert leader.tenant is self.tenant  # the same loaded tenant, nothing more to share

    async def teardown(self) -> None:
        return None

    async def run(self, case: Case) -> Output:
        assert isinstance(case.input, dict)
        return await asyncio.to_thread(self._retrieve, case.id, str(case.input["query"]))

    def _retrieve(self, case_id: str, query: str) -> Output:
        payload: dict[str, Any] = {
            "query": query,
            "scope": {"user_id": USER},
            "top_k": TOP_K,
            "include_pinned": False,
        }
        if self.rerank:
            payload["rerank"] = True
        r = self.tenant.c.http.post("/v1/retrieve", json=payload, headers=self.tenant.host)
        if r.status_code != 200:
            raise TargetError("http_status", f"HTTP {r.status_code}: {r.text[:500]}")
        body = r.json()
        if body["degraded"]:
            raise TargetError("invalid_output", f"degraded: {body['degraded']}")
        hits = [{"id": self.keys.get(i["id"], f"unknown:{i['id']}")} for i in body["items"]]
        return Output(case_id=case_id, value=hits)


def write_dataset(root: Path) -> Path:
    """The labelled queries as a lite-eval dataset (generated: corpus.py stays the one source)."""
    directory = root / "datasets" / "steward-recall"
    directory.mkdir(parents=True)
    meta = {
        "name": "steward-recall",
        "version": "1",
        "pack": "retrieval",
        "synthetic": True,
        "license": "Apache-2.0",
        "description": "tests/eval/corpus.py QUERIES (SYNTHETIC, Nimbus Outfitters), generated",
    }
    (directory / "dataset.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), "utf-8")
    lines = [
        json.dumps(
            {"id": case_id(n), "input": {"query": query}, "expected": {"relevant": expected}},
            ensure_ascii=False,
        )
        for n, (query, expected) in enumerate(QUERIES, start=1)
    ]
    (directory / "cases.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    return directory


def write_suite(root: Path, names: Sequence[str]) -> Path:
    suite = {
        "schema_version": 1,
        "name": "steward-recall",
        "pack": "retrieval",
        "dataset": "../datasets/steward-recall",
        "targets": [
            {
                "python": "tests.eval.target:built_by_the_test",  # recorded; built in `evaluate`
                "configs": [{"name": name} for name in names],
            }
        ],
        "scorers": [METRIC],
        "concurrency": 1,
        "cache": "off",  # a measurement of this build, never an older run's outputs
        # recall gates `plain` only (rerank is reported); no config may have an errored case
        "gate": [
            {"metric": METRIC, "min": THRESHOLD, "configs": ["plain"]},
            {"metric": "errors", "max": 0},
        ],
    }
    path = root / "suites" / "steward-recall.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(suite, sort_keys=False), "utf-8")
    return path


def built_by_the_test(**config: Any) -> StewardTarget:
    """What the recorded suite names: its targets need a running app, so they are built by
    `evaluate`, never from the suite file."""
    raise ConfigError(
        f"the steward-recall suite is generated by `uv run pytest -m eval`; run that ({config})"
    )


def evaluate(
    tenant: Tenant,
    keys: dict[str, str],
    *,
    db: str,
    root: Path,
    configs: Sequence[str] = tuple(CONFIGS),
) -> list[RunResult]:
    """One run per config on one loaded tenant, in the order given: by default `plain` (gated)
    and `rerank` (reported)."""
    write_dataset(root)
    suite = load_suite(write_suite(root, configs), root)
    dataset = load_dataset(suite.dataset_path)
    targets = [(name, StewardTarget(tenant, keys, db=db, rerank=CONFIGS[name])) for name in configs]
    results = asyncio.run(execute(suite, dataset, targets, RunOptions(ci=False)))
    order = {name: i for i, name in enumerate(configs)}
    return sorted(results, key=lambda r: order[str(r.record.target.config["name"])])


def legacy_scores(tenant: Any, keys: dict[str, str], **body: Any) -> dict[str, float]:
    """Each query's recall by the frozen original: `_recall` given that one query (its mean is
    then the query's score). For comparing case by case, not only the mean (Codex, P8)."""
    from tests.eval import legacy_recall

    scores = {}
    everything = legacy_recall.QUERIES
    try:
        for n, query in enumerate(everything, start=1):
            legacy_recall.QUERIES = [query]
            scores[case_id(n)], _ = legacy_recall._recall(tenant, keys, **body)
    finally:
        legacy_recall.QUERIES = everything
    return scores


def run_scores(result: RunResult) -> dict[str, float]:
    return {s.case_id: s.value for s in result.scores}


def misses(result: RunResult) -> list[str]:
    """The queries a run did not fully recall, as the original test printed them."""
    outputs = {o.case_id: o for o in load_run(result.directory).outputs}
    scores = run_scores(result)
    lines = []
    for n, (query, expected) in enumerate(QUERIES, start=1):
        if scores[case_id(n)] < 1:
            value = outputs[case_id(n)].value or []
            assert isinstance(value, list)
            got = sorted({h["id"] for h in value if not str(h["id"]).startswith("unknown:")})
            lines.append(f"{query!r}: wanted {expected}, got {got}")
    return lines
