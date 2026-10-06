"""End-to-end smoke test against a RUNNING service (used by CI against the Docker Compose stack, and usable locally):
readiness → create run → follow the SSE stream → verify the final run record and that a real database persisted it.
Usage: python scripts/smoke.py http://localhost:8000   (token from AGENT_API_TOKEN)"""
import json, os, sys, time, urllib.request

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
H = {"Authorization": "Bearer " + os.environ.get("AGENT_API_TOKEN", ""), "Content-Type": "application/json"}


def call(method, path, body=None, headers=H):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def fail(msg):
    print("SMOKE FAIL:", msg)
    sys.exit(1)


end = time.time() + 60
while True:                                              # wait for readiness (db migrated, pool open)
    try:
        r = call("GET", "/readyz", headers={})
        if r["status"] == "ready":
            break
    except Exception:
        pass
    if time.time() > end:
        fail("service never became ready")
    time.sleep(1)
print("ready:", json.dumps(r["config"]))
if r["config"]["store"] != "postgres":
    fail("expected the postgres store")

try:
    call("GET", "/v1/runs", headers={})
    fail("unauthenticated request was accepted")
except urllib.error.HTTPError as e:
    assert e.code == 401, e.code
print("auth enforced: 401 without token")

run = call("POST", "/v1/runs", {"objective": "compute 6 x 7"})
req = urllib.request.Request(f"{base}/v1/runs/{run['id']}/stream", headers=H)
events = []
with urllib.request.urlopen(req, timeout=30) as resp:
    cur = {}
    for raw in resp:
        line = raw.decode().rstrip("\n")
        if line == "":
            if cur:
                events.append(cur); cur = {}
            continue
        if line.startswith(":"):
            continue
        k, _, v = line.partition(": ")
        cur[k] = v
types = [e["event"] for e in events]
print("stream:", " → ".join(types))
for need in ("run_started", "model_request", "tool_requested", "tool_completed", "evaluation", "run_completed"):
    if need not in types:
        fail(f"missing event {need}")
done = call("GET", f"/v1/runs/{run['id']}")
if done["status"] != "completed" or "42" not in (done["final_answer"] or ""):
    fail(f"unexpected result {done['status']} {done['final_answer']}")
ev = call("POST", "/v1/evals/run")
print("eval suite:", json.dumps(ev["aggregate"]))
print("SMOKE OK: run", run["id"], "persisted;", len(events), "events streamed")
