"""astrolift-sample-stateful — the statefulset + PVC validation fixture.

A minimal-but-real FastAPI key/value service that persists to a SQLite DB
on a PVC-mounted path. It exists to prove two things in the spec 41
core-loop campaign:

  1. State survives a pod restart  → write a key, kill the pod, read it back
     (the StatefulSet re-binds the same PVC, so the row is still there).
  2. PVC teardown is clean          → deleting the deployment releases/deletes
     the claim with no dangling volume (campaign VERIFY-CLEAN).

Routes:
  PUT  /kv/{key}   body = raw value  → persist a value
  GET  /kv/{key}                     → read a value (404 if absent)
  GET  /kv                           → list all keys
  GET  /health                       → 200 readiness/liveness probe
  GET  /debug                        → spec 42 §3.1 diagnostic JSON
                                       (incl. store path + row count)

See spec 42 §3 (conventions / debug contract) and §4 (statefulset skeleton).
"""

from __future__ import annotations

import datetime
import logging
import os
import socket
import time

from fastapi import FastAPI, HTTPException, Request, Response

from app.store import KVStore, store_path

APP_NAME = "sample-stateful"
KIND = "statefulset"

# Resolved once at import: the git sha baked in at build time (see Dockerfile
# ARG GIT_SHA -> ENV APP_VERSION). Falls back to "dev" for local runs.
VERSION = os.environ.get("APP_VERSION", "dev")
BUILD_IMAGE = os.environ.get("BUILD_IMAGE", "")
BUILT_AT = os.environ.get("BUILT_AT", "")

# Non-secret env keys we advertise in /debug. We never echo values for keys
# that could carry secrets; for this fixture STORE_PATH/PORT/LOG_LEVEL are
# safe to surface by presence.
_DEBUG_ENV_KEYS = ("LOG_LEVEL", "PORT", "STORE_PATH", "APP_VERSION")

_START_MONOTONIC = time.monotonic()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "info").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(APP_NAME)

store = KVStore(store_path())
app = FastAPI(title=APP_NAME, version=VERSION)


@app.on_event("startup")
def _startup_banner() -> None:
    """Structured startup banner (spec 42 §3.1) — logs are useful at boot."""
    log.info(
        "startup app=%s version=%s kind=%s store_path=%s rows=%d port=%s",
        APP_NAME,
        VERSION,
        KIND,
        store.path,
        store.count(),
        os.environ.get("PORT", "8080"),
    )


@app.put("/kv/{key}")
async def put_value(key: str, request: Request) -> dict[str, object]:
    """Persist a value for ``key``. Body is the raw value bytes."""
    raw = await request.body()
    value = raw.decode("utf-8")
    store.put(key, value)
    log.info("put key=%s bytes=%d rows=%d", key, len(raw), store.count())
    return {"key": key, "value": value, "rows": store.count()}


@app.get("/kv/{key}")
def get_value(key: str) -> dict[str, str]:
    """Read a value. 404 when the key was never written."""
    value = store.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"key {key!r} not found")
    return {"key": key, "value": value}


@app.get("/kv")
def list_values() -> dict[str, object]:
    """List every persisted key/value pair plus the total row count."""
    items = store.list()
    return {"count": len(items), "items": items}


@app.get("/health")
def health() -> Response:
    """Readiness/liveness probe target. 200 once the store is openable.

    We touch the store (count) so a probe failure also catches a PVC that
    mounted but is not writable/readable — not just process liveness.
    """
    store.count()
    return Response(status_code=200, content="ok", media_type="text/plain")


@app.get("/debug")
def debug() -> dict[str, object]:
    """Diagnostic surface — spec 42 §3.1 contract, plus store path + row
    count so persistence is directly observable across a restart."""
    return {
        "app": APP_NAME,
        "version": VERSION,
        "kind": KIND,
        "hostname": socket.gethostname(),
        "uptime_s": round(time.monotonic() - _START_MONOTONIC, 3),
        "now": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "env_seen": [k for k in _DEBUG_ENV_KEYS if k in os.environ],
        "bindings": {"pvc": "present" if os.path.isdir(os.path.dirname(store.path)) else "absent"},
        "store": {"path": store.path, "rows": store.count()},
        "build": {"image": BUILD_IMAGE, "built_at": BUILT_AT, "commit": VERSION},
    }
