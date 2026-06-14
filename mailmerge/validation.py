"""Address validation: stdlib syntax check plus optional MX lookups."""

from __future__ import annotations

from functools import lru_cache

from .utils import valid_email_format


def valid_format(addr: str) -> bool:
    return valid_email_format(addr)


@lru_cache(maxsize=2048)
def mx_ok(domain: str) -> bool:
    """True if the domain advertises an MX record.

    Requires dnspython. If it is not installed we return True (cannot check, so
    do not block). Results are cached per-domain for the life of the process.
    """
    try:
        import dns.resolver  # type: ignore
    except ImportError:
        return True
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False


def dns_available() -> bool:
    try:
        import dns.resolver  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def address_mx_ok(addr: str) -> bool:
    if "@" not in addr:
        return False
    return mx_ok(addr.rsplit("@", 1)[-1])
