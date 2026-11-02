"""Repositories, snapshots, browsing, analysis endpoints and access control (real database)."""

from tests.conftest import SMALL_REPO, add_member, drain_jobs, make_repo, upload_snapshot

API = "/api/v1"


def test_import_pipeline_and_browse(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    snap = upload_snapshot(client, h, repo["id"], SMALL_REPO)
    assert snap["stats"]["files"] == 3 and snap["stats"]["languages"]["python"]["files"] == 2
    assert snap["stats"]["chunks"] > 0
    sid = snap["id"]

    files = {f["path"]: f for f in client.get(f"{API}/snapshots/{sid}/files", headers=h).json()}
    assert set(files) == {"app/auth.py", "app/billing.py", "README.md"}
    assert files["app/billing.py"]["findings"].get("high") == 1  # SQL built with an f-string

    f = client.get(f"{API}/snapshots/{sid}/file", headers=h, params={"path": "app/billing.py"}).json()
    assert [s["qualified_name"] for s in f["symbols"]] == ["invoice_total", "run_report"]
    assert f["imports"][0]["targets"] == ["app/auth.py"]
    assert {x["rule"] for x in f["findings"]} >= {"sql-injection"}
    auth_file = client.get(f"{API}/snapshots/{sid}/file", headers=h, params={"path": "app/auth.py"}).json()
    assert auth_file["imported_by"] == ["app/billing.py"]

    syms = client.get(f"{API}/snapshots/{sid}/symbols", headers=h, params={"q": "password"}).json()
    assert [s["name"] for s in syms][:2] == ["hash_password", "check_password"] or {s["name"] for s in syms} == {
        "hash_password",
        "check_password",
    }
    hp = next(s for s in syms if s["name"] == "hash_password")
    detail = client.get(f"{API}/symbols/{hp['id']}", headers=h).json()
    assert [c["qualified_name"] for c in detail["callers"]] == ["check_password"]
    assert detail["code"].startswith("def hash_password")
    cp = client.get(f"{API}/symbols/{detail['callers'][0]['id']}", headers=h).json()
    assert [c["qualified_name"] for c in cp["callers"]] == ["run_report"]  # resolved across files via import

    explain = client.get(f"{API}/symbols/{hp['id']}/explain", headers=h).json()
    assert explain["provider"] == "offline" and not explain["generated"]
    assert any("Called by 1 function" in x for x in explain["facts"])

    overview = client.get(f"{API}/snapshots/{sid}/overview", headers=h).json()
    assert overview["stats"]["functions"] == 4
    assert overview["core_files"][0]["path"] == "app/auth.py"  # imported by billing → most central
    findings = client.get(f"{API}/snapshots/{sid}/findings", headers=h, params={"min_severity": "high"}).json()
    assert [x["rule"] for x in findings["items"]] == ["sql-injection"]
    assert findings["items"][0]["fix"]
    deps = client.get(f"{API}/snapshots/{sid}/dependencies", headers=h, params={"level": "file"}).json()
    assert {(e["source"], e["target"]) for e in deps["edges"]} == {("app/billing.py", "app/auth.py")}


def test_upload_validation_and_duplicates(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    r = client.post(
        f"{API}/repositories/{repo['id']}/snapshots:upload", headers=h, files={"file": ("x.zip", b"not a zip")}
    )
    assert r.status_code == 422
    upload_snapshot(client, h, repo["id"], SMALL_REPO)
    from app.code.archive import build_archive

    r = client.post(
        f"{API}/repositories/{repo['id']}/snapshots:upload",
        headers=h,
        files={"file": ("repo.zip", build_archive(SMALL_REPO), "application/zip")},
    )
    assert r.status_code == 409  # identical to the latest snapshot

    empty = upload_snapshot(client, h, repo["id"], {"logo.png": "x"}, label="empty", process=False)
    drain_jobs()
    failed = client.get(f"{API}/snapshots/{empty['id']}", headers=h).json()
    assert failed["status"] == "failed" and "no supported source files" in failed["error"]
    # the failed snapshot did not replace the latest good one
    assert client.get(f"{API}/repositories/{repo['id']}", headers=h).json()["latest_snapshot"]["label"] == "v1"


def test_github_import_requires_url(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    r = client.post(f"{API}/repositories/{repo['id']}/snapshots:github", headers=h, json={"ref": "main"})
    assert r.status_code == 422
    r = client.patch(f"{API}/repositories/{repo['id']}", headers=h, json={"github_url": "https://evil.example/a/b"})
    assert r.status_code == 422
    r = client.patch(f"{API}/repositories/{repo['id']}", headers=h, json={"github_url": "https://github.com/acme/shop"})
    assert r.status_code == 200 and r.json()["source_url"] == "https://github.com/acme/shop"
    r = client.post(f"{API}/repositories/{repo['id']}/snapshots:github", headers=h, json={"ref": "release/1.0"})
    assert r.status_code == 202 and r.json()["source"] == "github" and r.json()["ref"] == "release/1.0"


def test_compare_snapshots_reports_impact(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    v1 = upload_snapshot(client, h, repo["id"], SMALL_REPO, label="v1")
    changed = dict(SMALL_REPO)
    changed["app/auth.py"] = SMALL_REPO["app/auth.py"].replace("100_000", "600_000")
    changed["app/billing.py"] = SMALL_REPO["app/billing.py"].replace(
        "db.execute(f\"SELECT * FROM invoices WHERE month = '{month}'\")",
        'db.execute("SELECT * FROM invoices WHERE month = %s", (month,))',
    )
    v2 = upload_snapshot(client, h, repo["id"], changed, label="v2")
    assert v2["stats"]["embeddings_reused"] > 0  # unchanged chunks were not embedded again
    r = client.get(f"{API}/repositories/{repo['id']}/compare", headers=h, params={"base": v1["id"], "head": v2["id"]})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["files"]["modified"] == ["app/auth.py", "app/billing.py"]
    assert {s["qualified_name"] for s in c["symbols"]["changed"]} == {"hash_password", "run_report"}
    assert {s["qualified_name"] for s in c["impacted"]} == {"check_password"}  # run_report changed itself
    assert [f["rule"] for f in c["findings"]["fixed"]] == ["sql-injection"]
    assert c["risk"]["level"] in ("low", "medium", "high") and c["risk"]["reasons"]


def test_access_control(client, auth):
    owner, reader, outsider = auth("owner"), auth("reader"), auth("outsider")
    repo = make_repo(client, owner)
    add_member(client, owner, repo["workspace"]["id"], "reader@example.com", "reader")
    snap = upload_snapshot(client, owner, repo["id"], SMALL_REPO)
    sid = snap["id"]
    sym = client.get(f"{API}/snapshots/{sid}/symbols", headers=owner).json()[0]["id"]

    # Readers can browse and search but not import or delete.
    assert client.get(f"{API}/snapshots/{sid}/files", headers=reader).status_code == 200
    r = client.post(f"{API}/repositories/{repo['id']}/snapshots:github", headers=reader, json={})
    assert r.status_code == 403
    assert client.delete(f"{API}/repositories/{repo['id']}", headers=reader).status_code == 403

    # Outsiders see nothing: 404 (not 403), so existence is not revealed.
    for url in [
        f"{API}/repositories/{repo['id']}",
        f"{API}/repositories/{repo['id']}/snapshots",
        f"{API}/snapshots/{sid}",
        f"{API}/snapshots/{sid}/files",
        f"{API}/snapshots/{sid}/overview",
        f"{API}/snapshots/{sid}/findings",
        f"{API}/symbols/{sym}",
        f"{API}/symbols/{sym}/explain",
        f"{API}/repositories/{repo['id']}/compare?base={sid}&head={sid}",
    ]:
        assert client.get(url, headers=outsider).status_code == 404, url
    assert client.get(f"{API}/repositories", headers=outsider).json() == []
    r = client.post(f"{API}/search", headers=outsider, json={"query": "hash password", "rerank": False})
    assert r.status_code == 200 and r.json()["results"] == []
    r = client.post(f"{API}/search", headers=outsider, json={"query": "x", "repository_ids": [repo["id"]]})
    assert r.status_code == 404
    r = client.post(f"{API}/ask", headers=outsider, json={"question": "How are passwords hashed?"})
    assert r.json()["status"] == "not_found" and r.json()["citations"] == []

    # Only owners delete repositories; deleting removes everything.
    assert client.delete(f"{API}/repositories/{repo['id']}", headers=owner).status_code == 204
    assert client.get(f"{API}/snapshots/{sid}", headers=owner).status_code == 404


def test_unauthenticated_requests_rejected(client):
    for method, url in [("get", "/repositories"), ("post", "/search"), ("post", "/ask"), ("get", "/symbols/1")]:
        assert getattr(client, method)(f"{API}{url}").status_code == 401
