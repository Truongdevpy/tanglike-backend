import ipaddress
import logging
import re
import socket
import unicodedata
import urllib.parse
from typing import Optional, Set

from app.config.settings import settings

logger = logging.getLogger(__name__)

DANGEROUS_HOSTNAMES: Set[str] = {
    "localhost",
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "100.100.100.200",
    "169.254.169.250",
    "kubernetes.default",
    "kubernetes.default.svc",
}

DANGEROUS_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".localdomain",
    ".home.arpa",
    ".svc.cluster.local",
)

DISALLOWED_PORTS: Set[int] = {
    20, 21, 22, 23, 25, 53, 69, 110, 111, 135, 137, 138, 139, 143,
    161, 389, 445, 587, 636, 993, 995, 1433, 1521, 2375, 2376,
    3306, 5432, 6379, 8500, 9200, 11211, 27017, 28017
}

RESTRICTED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("198.18.0.0/15"),    # Benchmark tests (RFC 2544)
    ipaddress.ip_network("192.0.0.0/24"),     # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),     # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    ipaddress.ip_network("2001:db8::/32"),    # Documentation IPv6
    ipaddress.ip_network("100::/64"),         # Discard prefix IPv6
]

DANGEROUS_SCHEMES: Set[str] = {
    "javascript", "data", "file", "vbscript", "about", "blob",
    "filesystem", "chrome", "chrome-extension", "ms-appx", "mhtml",
    "view-source", "wscript", "cscript", "ftp", "gopher", "ldap",
    "dict", "tftp", "jar", "ws", "wss"
}

DOMAIN_REGEX = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)+$")


def parse_ip_representation(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Parses standard, integer, hex, octal, and IPv6 representations into an ip_address object."""
    h = host.strip("[]").strip()
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass

    try:
        if h.isdigit() or h.lower().startswith("0x"):
            val = int(h, 0)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
    except (ValueError, OverflowError):
        pass

    parts = h.split(".")
    if 1 < len(parts) <= 4:
        try:
            num_parts = []
            for p in parts:
                p_clean = p.strip()
                if p_clean.lower().startswith("0x"):
                    num_parts.append(int(p_clean, 16))
                elif p_clean.startswith("0") and len(p_clean) > 1 and p_clean.isdigit():
                    num_parts.append(int(p_clean, 8))
                else:
                    num_parts.append(int(p_clean, 10))
            if len(num_parts) == 4 and all(0 <= p <= 255 for p in num_parts):
                return ipaddress.IPv4Address(".".join(str(p) for p in num_parts))
        except (ValueError, OverflowError):
            pass

    return None


def is_ip_restricted(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Returns True if the IP is private, loopback, link-local, multicast, or restricted."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and getattr(ip_obj, "ipv4_mapped", None):
        return is_ip_restricted(ip_obj.ipv4_mapped)

    if (
        ip_obj.is_loopback
        or ip_obj.is_private
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
    ):
        return True

    for net in RESTRICTED_NETWORKS:
        if net.version == ip_obj.version and ip_obj in net:
            return True

    return False


def is_safe_url(url: str, allow_local_in_dev: bool = False, is_production: Optional[bool] = None) -> bool:
    """
    Canonical SSRF and URL validator for outbound backend network requests.
    Guards against loopback, private ranges, link-local, cloud metadata,
    non-HTTP schemes, userinfo host-confusion, dangerous ports, and CRLF/null bytes.
    """
    if not url or not isinstance(url, str):
        return False

    url_clean = url.strip()
    if len(url_clean) < 8 or len(url_clean) > 2048:
        return False

    # Reject literal and encoded CRLF or null bytes
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", url_clean):
        return False
    lower_raw = url_clean.lower()
    if "%00" in lower_raw or "%0d" in lower_raw or "%0a" in lower_raw:
        return False

    # Unicode normalization (NFKC)
    normalized = unicodedata.normalize("NFKC", url_clean)
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", normalized):
        return False

    # Check double-decoding for hidden characters
    unquoted_1 = urllib.parse.unquote(normalized)
    unquoted_2 = urllib.parse.unquote(unquoted_1)
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", unquoted_2):
        return False
    if any("%00" in s.lower() for s in (unquoted_1, unquoted_2)):
        return False

    try:
        parsed = urllib.parse.urlsplit(normalized)
    except Exception:
        return False

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False

    prod = (settings.APP_ENV == "production") if is_production is None else is_production
    if prod and scheme != "https":
        return False

    # Disallow userinfo credentials in authority
    if parsed.username or parsed.password or "@" in parsed.netloc:
        return False

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return False

    # Validate port
    try:
        port = parsed.port
    except ValueError:
        return False

    if port is not None:
        if not (1 <= port <= 65535):
            return False
        if port in DISALLOWED_PORTS:
            return False

    # Check dangerous hostnames
    if hostname in DANGEROUS_HOSTNAMES:
        if allow_local_in_dev and not prod and hostname in ("localhost", "127.0.0.1"):
            pass
        else:
            return False

    # Check dangerous internal suffixes
    if any(hostname.endswith(suf) for suf in DANGEROUS_SUFFIXES):
        if not (allow_local_in_dev and not prod and hostname.endswith(".localhost")):
            return False

    # Check if host directly represents an IP
    ip_obj = parse_ip_representation(hostname)
    if ip_obj is not None:
        if is_ip_restricted(ip_obj):
            if allow_local_in_dev and not prod and hostname in ("localhost", "127.0.0.1"):
                pass
            else:
                return False

    # Attempt DNS resolution
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if is_ip_restricted(ip_obj):
                if allow_local_in_dev and not prod and hostname in ("localhost", "127.0.0.1"):
                    continue
                return False
    except socket.gaierror:
        if prod:
            return False
    except Exception:
        return False

    return True


def validate_target_link(link: str) -> bool:
    """
    Canonical validator for user-submitted target links/IDs in orders.
    Rejects dangerous pseudo-protocols, encoded schemes, fullwidth bypasses,
    CRLF, and embedded credentials, while supporting legitimate URLs and handles.
    """
    if not link or not isinstance(link, str):
        return False

    cleaned = link.strip()
    if len(cleaned) < 3 or len(cleaned) > 2000:
        return False

    # Disallow literal control characters, CRLF, and null bytes
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", cleaned):
        return False

    # Disallow encoded null bytes or CRLF
    lower_raw = cleaned.lower()
    if "%00" in lower_raw or "%0d" in lower_raw or "%0a" in lower_raw:
        return False

    # Normalize Unicode (NFKC)
    normalized = unicodedata.normalize("NFKC", cleaned)
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", normalized):
        return False

    # Check double-decoding for obfuscated controls or schemes
    unquoted_1 = urllib.parse.unquote(normalized)
    unquoted_2 = urllib.parse.unquote(unquoted_1)
    if re.search(r"[\r\n\x00-\x1f\x7f-\x9f]", unquoted_2):
        return False
    if "%00" in unquoted_1.lower() or "%00" in unquoted_2.lower():
        return False

    # Inspect scheme pattern across all decoded representations
    for text_form in (cleaned, normalized, unquoted_1, unquoted_2):
        lower_text = text_form.lower().strip()
        m = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*)\s*:", lower_text)
        if m:
            scheme = m.group(1).lower()
            if scheme in DANGEROUS_SCHEMES or scheme not in ("http", "https"):
                return False
        if any(lower_text.startswith(ds + ":") for ds in DANGEROUS_SCHEMES):
            return False

    try:
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.scheme:
            if parsed.scheme.lower() not in ("http", "https"):
                return False
            if parsed.username or parsed.password:
                return False
        else:
            first_segment = normalized.split("/")[0].split("?")[0].split("#")[0]
            if ":" in first_segment:
                return False
    except Exception:
        return False

    return True


def is_safe_presentation_url(url: Optional[str]) -> bool:
    """Validates public system presentation URLs (such as zalo_url) against dangerous schemes."""
    if not url:
        return True
    clean = str(url).strip()
    if not clean:
        return True
    if len(clean) > 500:
        return False
    # Allow pure phone numbers e.g. 0987654321 or +84987654321
    if re.match(r"^\+?[0-9\s\-]{8,20}$", clean):
        return True
    return validate_target_link(clean)


def is_safe_subsite_domain(domain: str) -> bool:
    """Validates reseller subsite domains to prevent IP registration or internal hostname collisions."""
    if not domain or not isinstance(domain, str):
        return False
    clean = domain.strip().lower()
    if len(clean) < 3 or len(clean) > 253:
        return False
    if not DOMAIN_REGEX.match(clean):
        return False
    # Disallow IP addresses as subsite domains
    parts = clean.split(".")
    if all(p.isdigit() for p in parts):
        return False
    if parse_ip_representation(clean) is not None:
        return False
    tld = parts[-1]
    if len(tld) < 2 or not any(c.isalpha() for c in tld):
        return False
    if clean in DANGEROUS_HOSTNAMES or clean in ("0.0.0.0", "127.0.0.1"):
        return False
    if any(clean.endswith(suf) for suf in DANGEROUS_SUFFIXES):
        return False
    return True


def sanitize_search_query(search: Optional[str], max_length: int = 100) -> Optional[str]:
    """Sanitizes user search inputs by stripping null bytes, CRLF, control chars, and bounding length."""
    if not search:
        return None
    cleaned = str(search).strip()
    if not cleaned:
        return None
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", cleaned)
    cleaned = re.sub(r"(?i)%00", "", cleaned)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = cleaned[:max_length].strip()
    return cleaned if cleaned else None
DISPOSABLE_EMAIL_DOMAINS: Set[str] = {
    "mailinator.com", "tempmail.com", "guerrillamail.com", "10minutemail.com",
    "sharklasers.com", "yopmail.com", "trashmail.com", "dispostable.com",
    "fakemailgenerator.com", "getairmail.com", "mohmal.com", "inboxkitten.com",
    "crazymailing.com", "temp-mail.org", "throwawaymail.com", "mytemp.email",
    "dropmail.me", "nada.ltd", "burnermail.io", "guerrillamailblock.com"
}

def is_disposable_email(email: str) -> bool:
    """Return True if email belongs to a known temporary/disposable domain."""
    if not email or "@" not in email:
        return False
    domain = email.split("@")[-1].lower().strip()
    return domain in DISPOSABLE_EMAIL_DOMAINS
