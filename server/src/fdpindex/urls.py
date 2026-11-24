"""When two client URLs name the same FDP.

The reference index keys an entry on the client URL exactly as sent, so
``https://fdp.example.org``, ``https://fdp.example.org/`` and
``http://fdp.example.org`` become three FDPs. Imports from a reference index
carry those duplicates over: home.fairdatapoint.org lists ten such pairs.

This index matches entries on :func:`url_key` instead. It ignores the
scheme (http or https), letter case in the scheme and host, the scheme's
default port, trailing slashes and a fragment. The path, its case and the
query are kept. The entry keeps the URL it was registered with. This is a
deviation from the reference (ADR-0001): a ping for a variant of a known
URL updates that entry instead of adding one.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def url_key(url: str) -> str:
    """The form under which two client URLs count as the same FDP."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host if port is None or port == _DEFAULT_PORTS.get(scheme) else f"{host}:{port}"
    key = f"//{netloc}{parts.path.rstrip('/')}"
    return f"{key}?{parts.query}" if parts.query else key
