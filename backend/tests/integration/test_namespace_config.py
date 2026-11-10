"""Config as code (design §8): `steward ns apply` validates before it writes; services read the
validated model."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from llm_steward.errors import Unprocessable
from llm_steward.models import Namespace
from llm_steward.namespace_config import InvalidStoredConfig
from tests.conftest import AppClient


def test_apply_creates_then_replaces_the_whole_config(app_client: AppClient) -> None:
    svc = app_client.services.namespaces
    first = {"retrieval": {"top_k": 3}, "kb": {"high_risk_keywords": ["法律"]}}
    namespace, created = app_client.call(svc.apply_config, "acme", first, "Acme")
    assert created
    namespace, created = app_client.call(svc.apply_config, "acme", {"retrieval": {"top_k": 5}})
    assert not created
    stored = app_client.get(Namespace, namespace.id)
    assert stored.config == {"retrieval": {"top_k": 5}}  # replaced, not merged
    assert stored.name == "Acme"  # kept when the file names none


def test_a_bad_config_changes_nothing(app_client: AppClient) -> None:
    svc = app_client.services.namespaces
    namespace, _ = app_client.call(svc.apply_config, "acme", {"retrieval": {"top_k": 3}})
    with pytest.raises(Unprocessable):
        app_client.call(svc.apply_config, "acme", {"retrieval": {"top_k": "lots"}})
    with pytest.raises(Unprocessable):
        app_client.call(svc.apply_config, "other", {"retreival": {}})
    assert app_client.get(Namespace, namespace.id).config == {"retrieval": {"top_k": 3}}
    with pytest.raises(Exception, match="not found"):
        app_client.call(svc.get_namespace, "other")  # a refused file creates nothing


def test_a_stored_config_that_no_longer_validates_is_a_named_error(app_client: AppClient) -> None:
    """Rows written before validation existed: an explicit problem, not a 500 deep in retrieval."""
    h, ns = app_client.new_tenant("acme")
    app_client.set_config(ns, {"retrieval": {"top_k": "many"}}, validate=False)
    r = app_client.http.post("/v1/retrieve", json={"query": "refund"}, headers=h)
    assert r.status_code == 500
    assert r.json()["code"] == InvalidStoredConfig.code
    assert "steward ns apply" in r.json()["detail"]


def test_the_cli_applies_and_shows_a_file(cli_db: str, tmp_path: Path) -> None:
    from llm_steward.cli import app

    good = tmp_path / "ns.yaml"
    good.write_text("slug: support-bot\nname: Support\nretrieval: {top_k: 4}\nkb:\n")
    runner = CliRunner()
    result = runner.invoke(app, ["ns", "apply", "-f", str(good)])
    assert result.exit_code == 0, result.output
    assert "support-bot" in result.output and "created" in result.output
    shown = runner.invoke(app, ["ns", "show", "support-bot"])
    assert shown.exit_code == 0, shown.output
    assert shown.output == "slug: support-bot\nname: Support\nkb: {}\nretrieval:\n  top_k: 4\n"

    bad = tmp_path / "bad.yaml"
    bad.write_text("slug: support-bot\nretrieval: {top_k: 0}\n")
    result = runner.invoke(app, ["ns", "apply", "-f", str(bad)])
    assert result.exit_code == 1
    assert "retrieval.top_k" in result.output
    assert "top_k: 4" in runner.invoke(app, ["ns", "show", "support-bot"]).output  # unchanged

    no_slug = tmp_path / "no-slug.yaml"
    no_slug.write_text("retrieval: {top_k: 4}\n")
    assert runner.invoke(app, ["ns", "apply", "-f", str(no_slug)]).exit_code == 1
