"""Prepare a fresh WeKnora v0.9.0 (compose profile `standard`) for llm-steward, and print the
settings llm-steward needs. Standard library only; safe to run again.

    set -a; . ./.env; set +a          # WEKNORA_ADMIN_EMAIL, WEKNORA_ADMIN_PASSWORD (root .env)
    python deploy/weknora/bootstrap.py >> backend/.env   # STEWARD_WEKNORA_* lines (gitignored)

1. Register the owner account (WeKnora has no default account) — already registered is fine.
2. Log in; find or register the embedding model (default `bge-m3:567m` on the host's Ollama,
   which compose passes as OLLAMA_BASE_URL) and wait until WeKnora reports it `active`.
3. Create a full-access workspace API key (a new one each run: keys are shown once).

Environment: WEKNORA_URL (default http://127.0.0.1:8680), WEKNORA_ADMIN_EMAIL,
WEKNORA_ADMIN_PASSWORD, WEKNORA_EMBEDDING_MODEL (default bge-m3:567m), WEKNORA_EMBEDDING_DIM
(default 1024). Facts: KB/global/weknora/README.md.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

URL = os.environ.get("WEKNORA_URL", "http://127.0.0.1:8680").rstrip("/") + "/api/v1"
MODEL = os.environ.get("WEKNORA_EMBEDDING_MODEL", "bge-m3:567m")
DIM = int(os.environ.get("WEKNORA_EMBEDDING_DIM", "1024"))


def call(method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        URL + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def fail(message: str) -> NoReturn:
    print(f"bootstrap: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    email = os.environ.get("WEKNORA_ADMIN_EMAIL")
    password = os.environ.get("WEKNORA_ADMIN_PASSWORD")
    if not (email and password):
        fail("set WEKNORA_ADMIN_EMAIL and WEKNORA_ADMIN_PASSWORD (root .env)")
    username = email.split("@")[0]
    account = {"username": username, "email": email, "password": password}
    status, _ = call("POST", "/auth/register", account)
    if status not in (200, 201, 400, 409):  # 400/409: already registered
        fail(f"register: HTTP {status}")

    status, login = call("POST", "/auth/login", {"email": email, "password": password})
    if status != 200 or not login.get("token"):
        fail(f"login: HTTP {status} (wrong WEKNORA_ADMIN_PASSWORD?)")
    token, tenant = login["token"], login["active_tenant"]["id"]

    _, models = call("GET", "/models", token=token)
    model = next(
        (m for m in models.get("data") or [] if m["name"] == MODEL and m["type"] == "Embedding"),
        None,
    )
    if model is None:
        body = {
            "name": MODEL,
            "display_name": MODEL,
            "type": "Embedding",
            "source": "local",
            "description": "llm-steward embeddings (host Ollama)",
            "parameters": {"embedding_parameters": {"dimension": DIM}},
        }
        status, created = call("POST", "/models", body, token)
        if status not in (200, 201):
            fail(f"register model {MODEL}: HTTP {status}")
        model = created["data"]
    for _ in range(120):  # a local model is pulled from Ollama first ("downloading")
        # Right after registration WeKnora can answer a poll with an error: not ready yet.
        status, current = call("GET", f"/models/{model['id']}", token=token)
        state = (current.get("data") or {}).get("status") or f"HTTP {status}"
        if state == "active":
            break
        if state == "download_failed":
            fail(f"WeKnora could not pull {MODEL} from Ollama (is it running on the host?)")
        time.sleep(2)
    else:
        fail(f"model {MODEL} still {state} after 4 minutes")

    status, key = call(
        "POST", f"/tenants/{tenant}/api-keys", {"name": "llm-steward", "full_access": True}, token
    )
    if status not in (200, 201):
        fail(f"create API key: HTTP {status}")
    print(f"STEWARD_WEKNORA_URL={URL.removesuffix('/api/v1')}")
    print(f"STEWARD_WEKNORA_API_KEY={key['data']['token']}")
    print(f"STEWARD_WEKNORA_EMBEDDING_MODEL_ID={model['id']}")


if __name__ == "__main__":
    main()
