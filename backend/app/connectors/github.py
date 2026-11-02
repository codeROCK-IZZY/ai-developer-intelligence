"""Download a public GitHub repository as a ZIP archive, safely.

The user supplies ``https://github.com/<owner>/<repo>`` and a ref (branch, tag or commit SHA).
The server never fetches an arbitrary URL: the address is rebuilt from the validated owner,
repository and ref, and only GitHub's archive hosts can be contacted. Defence in depth against
server-side request forgery (SSRF) is kept anyway:

* **Fixed host allow-list:** ``codeload.github.com`` (archives) and ``github.com`` (redirects).
* **HTTPS only**, port 443. The hostname is **resolved once and every address checked** —
  private, loopback, link-local and reserved addresses are refused — and the connection is
  **pinned to the checked IP** (TLS still verifies the hostname), so DNS rebinding cannot redirect it.
* **Redirects** are followed manually (max 3) and each target is re-validated.
* **Limits:** timeout, maximum download size, and the response must be a ZIP.

Private repositories are not supported (no credentials are stored); see docs/known-limitations.md.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from app.core.config import get_settings
from app.core.errors import UnprocessableData

ALLOWED_HOSTS = frozenset({"codeload.github.com", "github.com"})
ZIP_TYPES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")
MAX_REDIRECTS = 3
REPO_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
REF_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def parse_repo_url(url: str) -> GitHubRepo:
    m = REPO_URL_RE.match(url.strip())
    if not m or m.group(2) in (".", ".."):
        raise UnprocessableData("Enter a repository URL like https://github.com/owner/name")
    return GitHubRepo(m.group(1), m.group(2))


def validate_ref(ref: str) -> str:
    if not REF_RE.match(ref) or ".." in ref or ref.startswith(("/", "-")) or ref.endswith("/"):
        raise UnprocessableData("Invalid branch, tag or commit")
    return ref


def archive_url(repo: GitHubRepo, ref: str) -> str:
    ref = quote(validate_ref(ref), safe="/")
    return f"https://codeload.github.com/{quote(repo.owner)}/{quote(repo.name)}/zip/{ref}"


def _check_host(host: str) -> str:
    """Resolve ``host`` and return one safe IP address, or raise."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnprocessableData(f"Cannot resolve host {host}") from exc
    addresses = {str(info[4][0]) for info in infos}
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnprocessableData("This address is not allowed (private, internal or reserved network)")
    return sorted(addresses)[0]


def validate_url(url: str) -> tuple[str, str, str]:
    """Returns (host, path_with_query, pinned_ip)."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnprocessableData("Only https:// URLs are allowed")
    if parts.username or parts.password or parts.port not in (None, 443):
        raise UnprocessableData("URL not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if host not in ALLOWED_HOSTS:
        raise UnprocessableData(f"{host or 'This host'} is not an allowed GitHub host")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, path, _check_host(host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pre-validated IP, with SNI and certificate checks for the hostname."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


def download_archive(repo: GitHubRepo, ref: str) -> bytes:
    s = get_settings()
    limit = s.max_archive_mb * 1024 * 1024
    url = archive_url(repo, ref)
    for _ in range(MAX_REDIRECTS + 1):
        host, path, ip = validate_url(url)
        conn = _PinnedHTTPSConnection(host, ip, timeout=s.github_timeout_seconds)
        try:
            conn.request("GET", path, headers={"User-Agent": "DeveloperIntelligence/1.0", "Accept": "application/zip"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise UnprocessableData("Redirect without a location")
                url = urljoin(url, loc)
                continue
            if resp.status == 404:
                raise UnprocessableData("Repository or ref not found (only public repositories are supported)")
            if resp.status != 200:
                raise UnprocessableData(f"GitHub returned HTTP {resp.status}")
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in ZIP_TYPES:
                raise UnprocessableData(f"Unexpected content type {ctype or 'unknown'}")
            body = resp.read(limit + 1)
            if len(body) > limit:
                raise UnprocessableData(f"The repository archive is larger than {s.max_archive_mb} MB")
            return body
        except (OSError, http.client.HTTPException) as exc:
            raise UnprocessableData(f"Could not download the repository: {exc.__class__.__name__}") from exc
        finally:
            conn.close()
    raise UnprocessableData("Too many redirects")
