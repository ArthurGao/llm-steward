"""Fetch RAGFlow's own Docker deployment at the pinned tag and lock it down for local use.
Standard library only; safe to run again (existing passwords are kept).

    python deploy/ragflow/setup.py
    docker compose -p llm-steward-ragflow -f deploy/ragflow/upstream/docker-compose.yml up -d

RAGFlow's stack (Elasticsearch, MySQL, MinIO, Kvrocks, NATS and RAGFlow itself) is its own
compose project, used as upstream ships it: only `docker/.env` is changed, so an upgrade is a new
tag here plus a diff of upstream's `docker/`. Facts: KB/global/ragflow/README.md.

1. Download the tag's source archive from GitHub; unpack its `docker/` directory into
   `deploy/ragflow/upstream/` (gitignored; replaced on every run, `.env` excepted).
2. In `upstream/.env`: publish every port on 127.0.0.1 only (upstream binds all interfaces), move
   them off the ports this machine already uses, replace every password still at upstream's sample
   value with a random one (any other value is kept: the stack's data was made with it), and
   allow the host's Ollama (`ALLOW_ANY_HOST=1`, development only).
"""

import io
import re
import secrets
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

TAG = "v1.0.0"  # image infiniflow/ragflow:v1.0.0 (multi-arch); upgrade with the contract tests
ARCHIVE = f"https://codeload.github.com/infiniflow/ragflow/tar.gz/refs/tags/{TAG}"
HERE = Path(__file__).resolve().parent
UPSTREAM = HERE / "upstream"

# Host side of each published port (llm-steward's registry: KB/global/machine/ports.md).
PORTS = {
    "SVR_HTTP_PORT": 8690,  # the API llm-steward calls (STEWARD_RAGFLOW_URL)
    "SVR_WEB_HTTP_PORT": 8691,  # RAGFlow's web UI
    "ADMIN_SVR_HTTP_PORT": 8692,
    "SVR_WEB_HTTPS_PORT": 8693,
    "ES_PORT": 1200,
    "EXPOSE_MYSQL_PORT": 13306,
    "MINIO_PORT": 19000,
    "MINIO_CONSOLE_PORT": 19001,
    "KVROCKS_PORT": 16379,  # host side only: RAGFlow's container is pinned to 6379 upstream
    "EXPOSE_NATS_PORT": 14222,
    "EXPOSE_NATS_MONITORING_PORT": 18222,
}
PASSWORDS = (
    "ELASTIC_PASSWORD",
    "MYSQL_PASSWORD",
    "MINIO_PASSWORD",
    "REDIS_PASSWORD",
    "ADMIN_DEFAULT_PASSWORD",  # seeds RAGFlow's own admin superuser on the first start only
)
EXPECTED_IMAGE = f"RAGFLOW_IMAGE=infiniflow/ragflow:{TAG}"


def fetch() -> str:
    """Unpack the tag's `docker/`; returns upstream's own `.env` (its sample values)."""
    kept = (UPSTREAM / ".env").read_text() if (UPSTREAM / ".env").exists() else None
    print(f"setup: downloading RAGFlow {TAG} …", file=sys.stderr)
    with urllib.request.urlopen(ARCHIVE, timeout=300) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    shutil.rmtree(UPSTREAM, ignore_errors=True)
    UPSTREAM.mkdir()
    for member in archive.getmembers():
        parts = Path(member.name).parts  # <repo>-<sha>/docker/...
        if len(parts) < 3 or parts[1] != "docker" or not (member.isfile() or member.isdir()):
            continue
        target = UPSTREAM.joinpath(*parts[2:])
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        assert source is not None
        target.write_bytes(source.read())
        target.chmod(member.mode & 0o755 | 0o600)
    sample = (UPSTREAM / ".env").read_text()
    if kept is not None:  # keep the passwords the running stack was created with
        (UPSTREAM / ".env").write_text(kept)
    return sample


def lock_down(sample: str) -> None:
    env = UPSTREAM / ".env"
    text = env.read_text()
    if EXPECTED_IMAGE not in text:
        sys.exit(f"setup: upstream .env does not pin {EXPECTED_IMAGE}; check the tag")
    for name, port in PORTS.items():
        text = _set(text, name, f"127.0.0.1:{port}")
    for name in PASSWORDS:
        current = _value(text, name)
        if current is None:
            sys.exit(f"setup: {name} not found in upstream .env")
        if current == _value(sample, name):  # still upstream's sample: replace it
            text = _set(text, name, secrets.token_hex(16))
    text = _set(text, "ALLOW_ANY_HOST", "1")  # the host's Ollama at host.docker.internal
    env.write_text(text)
    env.chmod(0o600)


def _value(text: str, name: str) -> str | None:
    found = re.search(rf"^{name}=(.*)$", text, flags=re.M)
    return found.group(1) if found else None


def _set(text: str, name: str, value: str) -> str:
    updated, count = re.subn(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
    if count != 1:
        sys.exit(f"setup: expected one {name}= line in upstream .env, found {count}")
    return updated


def main() -> None:
    lock_down(fetch())
    where = UPSTREAM.relative_to(HERE.parent.parent)
    print(f"setup: RAGFlow {TAG} ready in {where}", file=sys.stderr)


if __name__ == "__main__":
    main()
