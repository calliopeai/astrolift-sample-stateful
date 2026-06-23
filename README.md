# astrolift-sample-stateful

The **statefulset + PVC** fixture for the Astrolift core-loop validation
campaign (spec [41](../../specs/41-core-loop-readiness-campaign.md), catalog
[42](../../specs/42-sample-fixtures.md) row **R7**). A minimal-but-real
FastAPI key/value service that persists to a SQLite database on a
PVC-mounted path.

It exists to prove two things end-to-end:

1. **State survives a pod restart** — write a key, kill the pod; the
   StatefulSet re-binds the *same* PersistentVolume, so the row is still
   there when the pod comes back.
2. **PVC teardown is clean** — tearing the deployment down releases/deletes
   the claim with no dangling volume (the campaign's `VERIFY-CLEAN` step).

## Topology

`kind = "statefulset"`, `replicas = 1`. The platform renders:

- a **StatefulSet** (`store`) whose pod runs the FastAPI app on port `8080`;
- a **headless Service** (`store-headless`, `clusterIP: None`) giving the
  pod stable DNS: `store-0.store-headless.<ns>.svc.cluster.local`;
- a regular **Service** (port `8080` is set, so a ClusterIP Service is
  emitted too);
- a **`volumeClaimTemplate`** named `store-data` (`1Gi`, `ReadWriteOnce`) so
  each pod gets its own PersistentVolume.

The app writes to `STORE_PATH` (default `/var/lib/store/data.db`), which is
the PVC mount point declared in `manifests/stateful.toml`.

## Endpoints

| Method | Path        | Behavior                                                       |
|--------|-------------|----------------------------------------------------------------|
| `PUT`  | `/kv/{key}` | Persist a value (request body = raw value). Returns row count. |
| `GET`  | `/kv/{key}` | Read a value. `404` if the key was never written.              |
| `GET`  | `/kv`       | List every persisted key/value pair + total count.             |
| `GET`  | `/health`   | `200 ok` once the store is openable (readiness/liveness probe).|
| `GET`  | `/debug`    | Diagnostic JSON — spec 42 §3.1 contract (see below).           |

### `/debug` (the diagnostic surface, spec 42 §3.1)

```json
{
  "app": "sample-stateful",
  "version": "main-abc1234",
  "kind": "statefulset",
  "hostname": "store-0",
  "uptime_s": 12.34,
  "now": "2026-06-22T19:11:30.250000+00:00",
  "env_seen": ["LOG_LEVEL", "STORE_PATH", "APP_VERSION"],
  "bindings": { "pvc": "present" },
  "store": { "path": "/var/lib/store/data.db", "rows": 2 },
  "build": { "image": "...", "built_at": "...", "commit": "main-abc1234" }
}
```

`store.path` + `store.rows` are the campaign's eyes on persistence: read
`/debug` before and after a restart and watch the row count hold.

A structured startup banner is logged at boot
(`startup app=... version=... store_path=... rows=...`), so the very first
log line tells you what's running and how much state it found.

## Verifying persistence across a restart

```bash
# 1. write a couple of keys
kubectl exec store-0 -- curl -s -X PUT --data 'one'  localhost:8080/kv/alpha
kubectl exec store-0 -- curl -s -X PUT --data 'two'  localhost:8080/kv/beta
kubectl exec store-0 -- curl -s localhost:8080/debug   # -> "rows": 2

# 2. kill the pod — the StatefulSet re-creates it and re-binds the SAME PVC
kubectl delete pod store-0
kubectl wait --for=condition=ready pod/store-0

# 3. the data is still there (proves the PVC, not ephemeral storage)
kubectl exec store-0 -- curl -s localhost:8080/kv/alpha   # -> {"key":"alpha","value":"one"}
kubectl exec store-0 -- curl -s localhost:8080/debug      # -> "rows": 2  ✅
```

`VERIFY-CLEAN`: after `astrolift app delete` (or `kubectl delete statefulset
store`), confirm the `store-data-store-0` PVC and its PV are gone with no
`Released`/`Terminating` leftovers.

## Run it locally

```bash
pip install -r requirements.txt
STORE_PATH=/tmp/store/data.db uvicorn app.main:app --port 8080
# the sqlite file is created lazily on the first PUT
curl -X PUT --data 'hello' localhost:8080/kv/greeting
curl localhost:8080/kv/greeting     # {"key":"greeting","value":"hello"}
curl localhost:8080/debug
```

Or with Docker (the image defaults `STORE_PATH=/var/lib/store/data.db`):

```bash
docker build -t astrolift-sample-stateful .
docker run -p 8080:8080 -v "$PWD/.data:/var/lib/store" astrolift-sample-stateful
```

## Build & publish

`.github/workflows/build.yml` builds and pushes to
`docker.io/calliopeai/astrolift-sample-stateful`, tagged `main-<sha7>`
(deterministic, for deploys) and `latest`, using the org-level secrets
`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`. PRs build to verify the Dockerfile;
only pushes to `main` publish. Mirrors `astrolift-agents` build conventions.

## Variant → matrix-cell map

| Manifest               | Matrix cell (spec 42 §6) |
|------------------------|--------------------------|
| `manifests/stateful.toml` | `statefulset` × `sample-stateful` |

## Note on the manifest `[[workloads.volumes]]` block

`manifests/stateful.toml` carries **both** the `storage_size` field **and**
a `[[workloads.volumes]]` pvc entry, exactly as the spec 42 §4 STATEFULSET
skeleton prescribes. As of the current backend renderer
(`astrolift_manifest/render.py`, verified 2026-06-22) these are **not yet
equivalent**:

- **`storage_size` is the load-bearing field.** It is what emits the
  StatefulSet's `volumeClaimTemplate` (named `<workload>-data`, i.e.
  `store-data`), with `accessModes: ["ReadWriteOnce"]` and
  `storageClassName` only when `storage_class` is non-empty.
- **The `[[workloads.volumes]]` entry is parsed and fully validated**
  (`parse_volume` / `VolumeDecl` — it requires an absolute `mount_path` and a
  parseable `size` for `kind="pvc"`) **but is not rendered** into the
  StatefulSet today: the renderer emits no `volumeMounts` on the container
  and no PodSpec `volumes` entry from it. The PVC's `mount_path` here is
  therefore declarative; it documents intent and matches `STORE_PATH`, but it
  does not yet drive an actual mount.

The mount point is wired by setting `STORE_PATH` to the same `/var/lib/store`
path so that, once the renderer mounts the `store-data` claim at that path
(or once `[[workloads.volumes]]` rendering lands), the SQLite DB lands on the
PVC with no app change required. Until then, treat this fixture as exercising
the **volumeClaimTemplate provisioning + headless-Service + clean-teardown**
path; the in-container mount of the claim is a known renderer gap (see the
report accompanying this fixture). The manifest is authored to the spec so it
is correct the moment the renderer catches up.
