"""SSRF protection — refuse agent-driven HTTP fetches to private/internal/metadata hosts.

Before any tool fetches a URL, resolve the hostname and block private, loopback,
link-local, CGNAT, and cloud-metadata targets (169.254.169.254 and friends — the endpoints
that hand out cloud credentials). Fail-closed: bad schemes and DNS failures are blocked.
Override broad private-IP blocking with `security.allow_private_urls` (or env
AEGIS_ALLOW_PRIVATE_URLS) — but cloud-metadata addresses stay blocked regardless, since
they are never a legitimate agent target.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Never legitimate, blocked even with the toggle on.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})
_METADATA_IPS = frozenset(ipaddress.ip_address(x) for x in (
    "169.254.169.254",        # AWS/GCP/Azure/DO/Oracle instance metadata
    "169.254.170.2",          # AWS ECS task metadata (task IAM creds)
    "169.254.169.253",        # Azure IMDS wire server
    "fd00:ec2::254",          # AWS metadata (IPv6)
    "100.100.100.200",        # Alibaba Cloud metadata
    "::ffff:169.254.169.254", "::ffff:169.254.170.2", "::ffff:169.254.169.253",
    "::ffff:100.100.100.200",
))
_METADATA_NETS = (
    ipaddress.ip_network("169.254.0.0/16"),          # entire link-local range
    ipaddress.ip_network("::ffff:169.254.0.0/112"),  # IPv4-mapped link-local
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")               # carrier-grade NAT (not is_private)


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip in _CGNAT)


def _allow_private(config) -> bool:
    if os.getenv("AEGIS_ALLOW_PRIVATE_URLS", "").strip().lower() in ("1", "true", "yes"):
        return True
    try:
        return bool(config and config.get("security.allow_private_urls", False))
    except Exception:  # noqa: BLE001
        return False


def _domain_match(host: str, pattern: str) -> bool:
    p = pattern.strip().lower().lstrip("*.")
    return bool(p) and (host == p or host.endswith("." + p))


def check_domain_policy(host: str, config) -> str:
    """'' when allowed; else why. ``web.deny_domains`` always wins; a non-empty
    ``web.allow_domains`` turns fetching into allowlist-only."""
    if config is None:
        return ""
    deny = config.get("web.deny_domains", []) or []
    allow = config.get("web.allow_domains", []) or []
    deny = deny if isinstance(deny, (list, tuple)) else []     # tolerate stub configs
    allow = allow if isinstance(allow, (list, tuple)) else []
    if any(_domain_match(host, d) for d in deny):
        return f"domain '{host}' is on web.deny_domains"
    if allow and not any(_domain_match(host, a) for a in allow):
        return f"domain '{host}' is not on web.allow_domains (allowlist mode)"
    return ""


def resolve_safe(url: str, config=None) -> tuple[str | None, str | None, str]:
    """Resolve ``url`` and validate every resolved IP against the SSRF policy.

    Returns ``(host, pinned_ip, reason)``. On success ``reason`` is empty and
    ``pinned_ip`` is a validated address the caller should connect to directly
    (closing the DNS-rebinding TOCTOU window — the host is resolved once here and
    the connection is pinned to this IP rather than re-resolved at connect time).
    On failure ``pinned_ip`` is ``None`` and ``reason`` explains why.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        return None, None, f"unparseable URL: {e}"
    if scheme not in ("http", "https"):
        return host or None, None, f"unsupported scheme '{scheme or '∅'}' (only http/https)"
    if not host:
        return None, None, "no host in URL"
    policy = check_domain_policy(host, config)
    if policy:
        return host, None, policy
    if host in _METADATA_HOSTS:
        return host, None, "cloud-metadata hostname"
    allow = _allow_private(config)
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return host, None, "DNS resolution failed"        # fail closed
    pinned: str | None = None
    for *_rest, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if ip in _METADATA_IPS or any(ip in n for n in _METADATA_NETS):
            return host, None, f"cloud-metadata address ({ip})"
        if not allow and _is_private_ip(ip):
            return host, None, f"private/internal address ({ip})"
        if pinned is None:
            pinned = str(ip)
    if pinned is None:
        return host, None, "DNS resolution failed"
    return host, pinned, ""


def is_safe_url(url: str, config=None) -> tuple[bool, str]:
    """Return (ok, reason). Resolves DNS and checks the actual target IP(s)."""
    _host, pinned, reason = resolve_safe(url, config)
    return (pinned is not None), reason


def guard(url: str, config=None) -> str | None:
    """Return None if the URL is safe to fetch, else an error message for the tool result."""
    ok, why = is_safe_url(url, config)
    if ok:
        return None
    return (f"blocked for safety: {why}. (SSRF protection — set "
            f"security.allow_private_urls=true to allow private hosts.)")


class BlockedURL(Exception):
    """Raised by :func:`request` when a URL (or a redirect target) is unsafe."""


def _pin_request_url(url: str, pinned_ip: str):
    """Return (connect_url, host) with the hostname swapped for the validated IP so
    the socket connects to the address we checked, not a re-resolved one. IPv6 gets
    bracketed. The original host is returned for the Host header / TLS SNI."""
    import httpx

    u = httpx.URL(url)
    host = u.host
    literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    return u.copy_with(host=literal), host


def request(method: str, url: str, config=None, *, headers=None, content=None,
            timeout: float = 30.0, max_redirects: int = 5):
    """A guarded HTTP request that re-validates EVERY redirect hop against the SSRF
    policy AND pins each connection to the IP it validated.

    ``httpx`` with ``follow_redirects=True`` would jump to a redirected Location
    without re-checking it (a public URL could 302 to a private/metadata host), and
    even a single checked URL is re-resolved by the socket at connect time — a
    DNS-rebinding server can answer the second lookup with an internal IP. We follow
    redirects manually, call :func:`resolve_safe` on each hop, and connect to the
    exact validated IP (carrying the original Host header and TLS SNI so virtual
    hosting and certificate verification still work). Returns the final
    ``httpx.Response``; raises :class:`BlockedURL` if any hop is unsafe."""
    import httpx

    method = method.upper()
    current = url
    base_headers = {"User-Agent": "Mozilla/5.0 (AEGIS)", **(headers or {})}
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            host, pinned_ip, reason = resolve_safe(current, config)
            if reason or pinned_ip is None:
                raise BlockedURL(reason or "could not resolve a safe address")
            connect_url, real_host = _pin_request_url(current, pinned_ip)
            req_headers = dict(base_headers)
            if real_host:
                req_headers["Host"] = real_host
            extensions = {"sni_hostname": real_host} if real_host else None
            resp = client.request(method, connect_url, content=content,
                                  headers=req_headers, extensions=extensions)
            location = resp.headers.get("location") if resp.is_redirect else None
            if not location:
                return resp
            # Resolve the next hop relative to the *real* URL, not the IP-pinned one.
            nxt = str(httpx.URL(current).join(location))
            current = nxt
            if resp.status_code in (301, 302, 303) and method not in ("GET", "HEAD"):
                method, content = "GET", None
    raise BlockedURL("too many redirects")
