"""Code search and questions about code, with workspace isolation (real database + local models)."""

from tests.conftest import SMALL_REPO, make_repo, upload_snapshot

API = "/api/v1"

OTHER_REPO = {
    "secrets/vault.py": (
        '"""Internal vault client."""\n\n\ndef hash_password(pw: str) -> str:\n'
        '    """Hash a password with the internal vault service."""\n    return "vault:" + pw\n'
    ),
}


def test_search_modes_and_filters(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    upload_snapshot(client, h, repo["id"], SMALL_REPO)
    for mode in ("keyword", "vector", "hybrid"):
        r = client.post(f"{API}/search", headers=h, json={"query": "hash a password", "mode": mode, "rerank": False})
        assert r.status_code == 200
        # check_password calls hash_password(password…), so keyword-only may rank it first
        assert "hash_password" in [x["symbol"] for x in r.json()["results"][:2]], mode
    r = client.post(f"{API}/search", headers=h, json={"query": "sales tax on invoices", "rerank": True})
    top = r.json()["results"][0]
    assert top["symbol"] == "invoice_total" and top["rerank_score"] is not None and r.json()["reranked"]
    r = client.post(f"{API}/search", headers=h, json={"query": "invoice", "language": "text", "rerank": False})
    assert {x["path"] for x in r.json()["results"]} == {"README.md"}
    r = client.post(f"{API}/search", headers=h, json={"query": "password", "path_prefix": "app/bill", "rerank": False})
    assert {x["path"] for x in r.json()["results"]} == {"app/billing.py"}


def test_ask_answers_with_citations_or_not_found(client, auth):
    h = auth("owner")
    repo = make_repo(client, h)
    upload_snapshot(client, h, repo["id"], SMALL_REPO)
    r = client.post(f"{API}/ask", headers=h, json={"question": "How are passwords hashed?"})
    body = r.json()
    assert body["status"] == "answered" and body["provider"] == "offline"
    assert body["citations"][0]["symbol"] == "hash_password" and body["citations"][0]["path"] == "app/auth.py"
    assert all(s["supported"] for s in body["sentences"])
    r = client.post(f"{API}/ask", headers=h, json={"question": "How do we train the recommendation model on GPUs?"})
    assert r.json()["status"] == "not_found" and r.json()["citations"] == []
    history = client.get(f"{API}/questions", headers=h).json()
    assert [q["status"] for q in history] == ["not_found", "answered"]


def test_no_leakage_between_workspaces(client, auth):
    """dana's question matches code in a workspace she does not belong to almost word for word;
    she must still only ever see her own repository."""
    dana, admin = auth("dana"), auth("admin")
    mine = make_repo(client, dana, workspace="Storefront", name="shop")
    upload_snapshot(client, dana, mine["id"], SMALL_REPO)
    theirs = make_repo(client, admin, workspace="Platform", name="vault")
    upload_snapshot(client, admin, theirs["id"], OTHER_REPO)

    r = client.post(f"{API}/search", headers=dana, json={"query": "hash a password with the vault", "limit": 20})
    assert r.json()["results"] and {x["repository_id"] for x in r.json()["results"]} == {mine["id"]}
    r = client.post(f"{API}/ask", headers=dana, json={"question": "How does the vault hash a password?"})
    assert {c["repository_id"] for c in r.json()["citations"]} <= {mine["id"]}
    # and the owner of the other workspace does find it
    r = client.post(f"{API}/search", headers=admin, json={"query": "vault password", "rerank": False})
    assert r.json()["results"][0]["repository_id"] == theirs["id"]
