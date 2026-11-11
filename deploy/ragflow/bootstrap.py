"""Prepare a fresh RAGFlow v1.0.0 (deploy/ragflow) for llm-steward, and print the settings
llm-steward needs. Safe to run again. Needs `cryptography` (in the backend's environment):

    set -a; . ./.env; set +a          # RAGFLOW_ADMIN_EMAIL, RAGFLOW_ADMIN_PASSWORD (root .env)
    uv run --project backend python deploy/ragflow/bootstrap.py >> backend/.env   # gitignored

1. Register the account (already registered is fine) and log in: the password travels
   RSA-encrypted with RAGFlow's public key, and the session comes back in a response header.
2. Add the Ollama provider, an instance on the host's Ollama serving the embedding model (default
   `bge-m3:567m`), and make that model the tenant's default: new datasets use it.
3. Mint an API key (a new one each run: keys are shown once).

Environment: RAGFLOW_URL (default http://127.0.0.1:8690), RAGFLOW_ADMIN_EMAIL,
RAGFLOW_ADMIN_PASSWORD, RAGFLOW_OLLAMA_URL (default http://host.docker.internal:11434, as RAGFlow's
container sees the host), RAGFLOW_EMBEDDING_MODEL (default bge-m3:567m). Facts:
KB/global/ragflow/README.md §1.4.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, NoReturn

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

BASE = os.environ.get("RAGFLOW_URL", "http://127.0.0.1:8690").rstrip("/")
URL = BASE + "/api/v1"
OLLAMA = os.environ.get("RAGFLOW_OLLAMA_URL", "http://host.docker.internal:11434")
MODEL = os.environ.get("RAGFLOW_EMBEDDING_MODEL", "bge-m3:567m")
INSTANCE = "llm-steward"
# RAGFlow's login key: conf/public.pem at v1.0.0 (also the web UI's rsaPsw). Public by design.
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/
z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp
2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOO
UEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVK
RNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK
6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs
2wIDAQAB
-----END PUBLIC KEY-----
"""
ALREADY_REGISTERED = 103


def encrypt(password: str) -> str:
    key = serialization.load_pem_public_key(PUBLIC_KEY)
    plain = base64.b64encode(password.encode())
    return base64.b64encode(key.encrypt(plain, padding.PKCS1v15())).decode()  # type: ignore[union-attr]


def call(
    method: str, path: str, body: Any = None, session: str | None = None
) -> tuple[int, Any, dict[str, str]]:
    """(RAGFlow's business code, data, response headers); HTTP errors carry a code too."""
    request = urllib.request.Request(
        URL + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"}
        | ({"Authorization": session} if session else {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload, headers = json.loads(response.read() or b"{}"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        payload, headers = json.loads(exc.read() or b"{}"), dict(exc.headers)
    return int(payload.get("code", -1)), payload.get("data"), headers


def fail(message: str) -> NoReturn:
    print(f"bootstrap: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    email = os.environ.get("RAGFLOW_ADMIN_EMAIL")
    password = os.environ.get("RAGFLOW_ADMIN_PASSWORD")
    if not (email and password):
        fail("set RAGFLOW_ADMIN_EMAIL and RAGFLOW_ADMIN_PASSWORD (root .env)")
    account = {"email": email, "nickname": email.split("@")[0], "password": encrypt(password)}
    code, _, _ = call("POST", "/users", account)
    if code not in (0, ALREADY_REGISTERED):
        fail(f"register: code {code}")
    code, _, headers = call("POST", "/auth/login", {"email": email, "password": encrypt(password)})
    session = headers.get("Authorization")
    if code != 0 or not session:
        fail(f"login: code {code} (wrong RAGFLOW_ADMIN_PASSWORD?)")

    code, _, _ = call("PUT", "/providers", {"provider_name": "Ollama"}, session)
    if code != 0:
        fail(f"add the Ollama provider: code {code}")
    code, instances, _ = call("GET", "/providers/Ollama/instances", session=session)
    if code != 0:
        fail(f"list Ollama instances: code {code}")
    if not any(i.get("instance_name") == INSTANCE for i in instances or []):
        body = {
            "instance_name": INSTANCE,
            "base_url": OLLAMA,
            "api_key": "",
            "model_info": [{"model_name": MODEL, "model_type": ["embedding"], "max_tokens": 8192}],
        }
        code, _, _ = call("POST", "/providers/Ollama/instances", body, session)
        if code != 0:
            fail(f"add the Ollama instance at {OLLAMA}: code {code}")
    default = {
        "model_provider": "Ollama",
        "model_instance": INSTANCE,
        "model_type": "embedding",
        "model_name": MODEL,
    }
    code, _, _ = call("PATCH", "/models/default", default, session)
    if code != 0:
        fail(f"make {MODEL} the default embedding model: code {code}")

    code, token, _ = call("POST", "/system/tokens", session=session)
    if code != 0 or not (token or {}).get("token"):
        fail(f"mint an API key: code {code}")
    print(f"STEWARD_RAGFLOW_URL={BASE}")
    print(f"STEWARD_RAGFLOW_API_KEY={token['token']}")


if __name__ == "__main__":
    main()
