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


def is_safe_url(url: str, config=None) -> tuple[bool, str]:
    """Return (ok, reason). Resolves DNS and checks the actual target IP(s)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
    except Exception as e:  # noqa: BLE001
        return False, f"unparseable URL: {e}"
    if scheme not in ("http", "https"):
        return False, f"unsupported scheme '{scheme or '∅'}' (only http/https)"
    if not host:
        return False, "no host in URL"
    policy = check_domain_policy(host, config)
    if policy:
        return False, policy
    if host in _METADATA_HOSTS:
        return False, "cloud-metadata hostname"
    allow = _allow_private(config)
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, "DNS resolution failed"        # fail closed
    for *_rest, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if ip in _METADATA_IPS or any(ip in n for n in _METADATA_NETS):
            return False, f"cloud-metadata address ({ip})"
        if not allow and _is_private_ip(ip):
            return False, f"private/internal address ({ip})"
    return True, ""


def guard(url: str, config=None) -> str | None:
    """Return None if the URL is safe to fetch, else an error message for the tool result."""
    ok, why = is_safe_url(url, config)
    if ok:
        return None
    return (f"blocked for safety: {why}. (SSRF protection — set "
            f"security.allow_private_urls=true to allow private hosts.)")
