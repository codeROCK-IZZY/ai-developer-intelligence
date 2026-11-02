"""GitHub import: URL/ref validation and SSRF protections (no network)."""

import socket

import pytest

from app.connectors import github
from app.core.errors import UnprocessableData


def test_repo_url_parsing():
    r = github.parse_repo_url("https://github.com/acme/shop-api.git")
    assert (r.owner, r.name, r.url) == ("acme", "shop-api", "https://github.com/acme/shop-api")
    for bad in [
        "http://github.com/acme/shop",  # not https
        "https://gitlab.com/acme/shop",
        "https://github.com/acme",
        "https://github.com/acme/shop/tree/main",
        "https://github.com.evil.example/acme/shop",
        "https://user:pw@github.com/acme/shop",
        "https://github.com/acme/..",
    ]:
        with pytest.raises(UnprocessableData):
            github.parse_repo_url(bad)


def test_ref_validation_and_archive_url():
    repo = github.GitHubRepo("acme", "shop")
    assert github.archive_url(repo, "release/2.4") == "https://codeload.github.com/acme/shop/zip/release/2.4"
    for bad in ["../../etc", "-rf", "main/", "a b", "x" * 300, "main?x=1"]:
        with pytest.raises(UnprocessableData):
            github.validate_ref(bad)


def test_only_github_hosts_are_contacted(monkeypatch):
    monkeypatch.setattr(github, "_check_host", lambda host: "140.82.112.10")
    assert github.validate_url("https://codeload.github.com/a/b/zip/main")[0] == "codeload.github.com"
    for bad in [
        "https://example.com/x.zip",
        "http://codeload.github.com/a",
        "https://codeload.github.com:8443/a",
        "https://169.254.169.254/latest",
    ]:
        with pytest.raises(UnprocessableData):
            github.validate_url(bad)


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.1"])
def test_private_addresses_refused(monkeypatch, addr):
    """DNS rebinding defence: even an allowed hostname is refused if it resolves internally."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, (addr, 443))])
    with pytest.raises(UnprocessableData):
        github._check_host("codeload.github.com")
