import base64
import hashlib

from app.modules.m7_archive import service

VAULT_PATH = "永久库/磁带-02/照片/冬港/14号栈桥.tif"


def start(client):
    response = client.post("/api/runs", json={"sample": "dirty"})
    assert response.status_code == 200, response.text
    return response.json()


def sign(client, run, reviewer="周洁"):
    return client.post(f'/api/runs/{run["id"]}/sign',
                       json={"reviewer": reviewer, "digest": run["manifest"]["digest"]})


def bridge_payload(dirty, observed="2026-11-20T16:25:00+08:00"):
    data = next(row["data_b64"] for row in dirty["replicas"]
                if row["path"] == "阅览池/冬港底片/14号栈桥.tif")
    return {"tier": "vault", "path": VAULT_PATH, "observed": observed, "data_b64": data}


def extra_object(ident="0030", name="补充登记.pdf"):
    payload = f"补件-{name}".encode()
    digest = hashlib.sha256(payload).hexdigest()
    return {"objects": [{"object_id": ident, "path": f"文书/船政/{name}",
                         "sha256": digest, "bytes": len(payload)}],
            "replicas": [{"tier": tier, "path": f"{prefix}/文书/船政/{name}",
                          "observed": "2026-11-20T16:25:00+08:00",
                          "data_b64": base64.b64encode(payload).decode()}
                         for tier, prefix in (("online", "阅览池/北港"), ("vault", "永久库/磁带-02"))]}


def supplement(dirty):
    addition = extra_object()
    return {"objects": addition["objects"],
            "replicas": [bridge_payload(dirty), *addition["replicas"]]}


def test_supplement_advances_revision_and_old_version_stays(client, dirty):
    base = start(client)
    assert sign(client, base).status_code == 200
    response = client.post(f'/api/runs/{base["id"]}/supplements', json=supplement(dirty))
    assert response.status_code == 200, response.text
    run = response.json()
    assert not run["reused"] and run["id"] != base["id"]
    assert run["manifest"]["parent"] == base["id"] and run["manifest"]["revision"] == 2
    assert run["manifest"]["supplement"]["objects"] == [len(dirty["objects"]) + 1]
    first = len(dirty["replicas"]) + 1
    assert run["manifest"]["supplement"]["replicas"] == [first, first + 1, first + 2]

    delta = run["delta"]
    assert delta["parent"] == base["id"]
    assert delta["summary"] == {"added": 1, "changed": 1, "removed": 0, "unchanged": 25}
    changes = {change["object_id"]: change for change in delta["changes"]}
    assert changes["0002"]["before"]["state"] == "pending"
    assert changes["0002"]["before"]["copies"]["vault"]["code"] == "REPLICA_MISSING"
    assert changes["0002"]["after"]["state"] == "verified"
    assert changes["0002"]["after"]["copies"]["vault"]["state"] == "verified"
    assert changes["0030"]["kind"] == "added" and changes["0030"]["before"] is None
    assert changes["0030"]["source_lines"] == [len(dirty["objects"]) + 1]
    assert delta["reconcile"]["before"]["totals"]["pending"] == 2
    assert delta["reconcile"]["after"]["totals"]["pending"] == 1
    assert run["result"]["reconcile"]["status"] == "MATCHED"

    old = client.get(f'/api/runs/{base["id"]}').json()
    assert old["signature"]["reviewer"] == "周洁"
    assert old["manifest"]["digest"] == base["manifest"]["digest"]
    assert old["result"] == base["result"] and old["delta"] is None

    exported = client.get(f'/api/runs/{run["id"]}/export').json()
    assert exported["delta"] == run["delta"] and exported["result"] == run["result"]
    assert exported["source"]["objects"][-1]["object_id"] == "0030"
    assert len(exported["source"]["replicas"]) == len(dirty["replicas"]) + 3

    assert sign(client, run, "李颖").status_code == 200
    assert len(client.get("/api/runs").json()["runs"]) == 2


def test_same_supplement_does_not_add_version(client, dirty):
    base = start(client)
    sign(client, base)
    payload = supplement(dirty)
    url = f'/api/runs/{base["id"]}/supplements'
    first = client.post(url, json=payload).json()
    second = client.post(url, json=payload).json()
    assert not first["reused"] and second["reused"] and second["id"] == first["id"]
    assert second["manifest"] == first["manifest"] and second["delta"] == first["delta"]
    assert len(client.get("/api/runs").json()["runs"]) == 2


def test_revision_chain_links_versions(client, dirty):
    base = start(client)
    first = client.post(f'/api/runs/{base["id"]}/supplements',
                        json={"replicas": [bridge_payload(dirty)]}).json()
    second = client.post(f'/api/runs/{first["id"]}/supplements',
                         json=extra_object("0031", "续补登记.pdf")).json()
    assert first["manifest"]["parent"] == base["id"] and first["manifest"]["revision"] == 2
    assert second["manifest"]["parent"] == first["id"] and second["manifest"]["revision"] == 3
    assert second["delta"]["parent"] == first["id"]
    assert second["delta"]["summary"]["added"] == 1 and second["delta"]["summary"]["changed"] == 0
    assert len(client.get("/api/runs").json()["runs"]) == 3


def test_supplement_rows_follow_same_state_chain(client, dirty):
    base = start(client)
    forged = dict(dirty["objects"][5])
    forged["sha256"] = "0" * 64
    run = client.post(f'/api/runs/{base["id"]}/supplements', json={"objects": [forged]}).json()
    change = next(item for item in run["delta"]["changes"] if item["object_id"] == "0006")
    assert change["before"]["state"] == "verified" and change["after"]["state"] == "conflict"
    line = len(dirty["objects"]) + 1
    assert any(issue["code"] == "OBJECT_CONFLICT" and issue["line"] == line
               for issue in run["result"]["reconcile"]["issues"])


def test_late_observation_needs_same_day_cutoff_extension(client, dirty):
    base = start(client)
    sign(client, base)
    url = f'/api/runs/{base["id"]}/supplements'
    late = bridge_payload(dirty, observed="2026-11-20T18:05:00+08:00")
    held = client.post(url, json={"replicas": [late]}).json()
    assert held["delta"]["summary"]["changed"] == 0
    assert any(issue["code"] == "FUTURE_OBSERVATION" for issue in held["result"]["reconcile"]["issues"])
    run = client.post(url, json={"replicas": [late], "at": "2026-11-20T20:00:00+08:00"}).json()
    assert run["manifest"]["parameters"]["as_of"] == "2026-11-20T20:00:00+08:00"
    change = next(item for item in run["delta"]["changes"] if item["object_id"] == "0002")
    assert change["after"]["state"] == "verified"


def test_failed_supplement_keeps_signed_version(client, dirty):
    base = start(client)
    sign(client, base)
    url = f'/api/runs/{base["id"]}/supplements'
    assert client.post(url, json={"objects": "不是数组"}).json()["code"] == "BAD_SUPPLEMENT"
    assert client.post(url, json={"objects": ["不是记录"]}).json()["code"] == "BAD_SUPPLEMENT"
    assert client.post(url, json={"objects": [], "replicas": []}).json()["code"] == "EMPTY_SUPPLEMENT"
    assert client.post(url, json={"surprise": []}).json()["code"] == "BAD_SUPPLEMENT"
    late = {"replicas": [bridge_payload(dirty)]}
    assert client.post(url, json={**late, "at": "2026-11-20T16:00:00+08:00"}).json()["code"] == "AT_REGRESSION"
    assert client.post(url, json={**late, "at": "没有时区"}).json()["code"] == "BAD_VALUE"
    assert client.post("/api/runs/missing/supplements", json=late).status_code == 404
    assert len(client.get("/api/runs").json()["runs"]) == 1
    intact = client.get(f'/api/runs/{base["id"]}').json()
    assert intact["signature"]["reviewer"] == "周洁" and intact["result"] == base["result"]


def test_failed_rerun_keeps_signed_version(client, monkeypatch):
    base = start(client)
    sign(client, base)
    monkeypatch.setattr(service, "evaluate", lambda source: {"forged": True})
    response = client.post(f'/api/runs/{base["id"]}/rerun')
    assert response.status_code == 409 and response.json()["code"] == "REPLAY_MISMATCH"
    intact = client.get(f'/api/runs/{base["id"]}').json()
    assert intact["signature"]["reviewer"] == "周洁"
    assert intact["manifest"] == base["manifest"] and intact["result"] == base["result"]


def test_only_balanced_revision_can_be_signed(client, clean):
    clean["objects"] = []
    empty = client.post("/api/runs", json={"source": clean}).json()
    assert empty["result"]["reconcile"]["status"] == "CHECK_REQUIRED"
    denied = sign(client, empty)
    assert denied.status_code == 409 and denied.json()["code"] == "UNBALANCED_RUN"
    assert client.get(f'/api/runs/{empty["id"]}').json()["signature"] is None
    run = client.post(f'/api/runs/{empty["id"]}/supplements', json=extra_object()).json()
    assert run["result"]["reconcile"]["status"] == "MATCHED"
    assert sign(client, run).status_code == 200
