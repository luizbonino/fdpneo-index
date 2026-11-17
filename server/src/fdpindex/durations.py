"""ISO-8601 durations as Java's ``java.time.Duration`` reads and writes them.

The reference API exchanges durations as strings produced by
``Duration.toString()``: hours, minutes and seconds only (7 days is
``PT168H``, zero is ``PT0S``). Parsing accepts what ``Duration.parse``
accepts: an optional day part and a time part (``P2DT3H4M5.5S``).
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal

_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d{1,9})?)S)?)?$",
    re.IGNORECASE,
)


def parse_duration(value: str) -> timedelta:
    """Parse ``value``; raises :class:`ValueError` for anything ``Duration.parse`` would reject."""
    text = value.strip()
    match = _PATTERN.match(text)
    if match is None or text.upper() in {"P", "PT"} or text.upper().endswith("T"):
        raise ValueError(f"not an ISO-8601 duration: {value!r}")
    parts = match.groupdict()
    if not any(parts.values()):
        raise ValueError(f"not an ISO-8601 duration: {value!r}")
    return timedelta(
        days=int(parts["days"] or 0),
        hours=int(parts["hours"] or 0),
        minutes=int(parts["minutes"] or 0),
        seconds=float(Decimal(parts["seconds"] or "0")),
    )


def format_duration(value: timedelta) -> str:
    """Format like ``Duration.toString()``: ``PT168H``, ``PT10M``, ``PT1M30S``, ``PT0S``."""
    total_us = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    if total_us == 0:
        return "PT0S"
    sign = "-" if total_us < 0 else ""
    total_us = abs(total_us)
    hours, rest = divmod(total_us, 3_600_000_000)
    minutes, rest = divmod(rest, 60_000_000)
    seconds, micros = divmod(rest, 1_000_000)
    out = "PT"
    if hours:
        out += f"{sign}{hours}H"
    if minutes:
        out += f"{sign}{minutes}M"
    if seconds or micros:
        fraction = f".{micros:06d}".rstrip("0") if micros else ""
        out += f"{sign}{seconds}{fraction}S"
    return out
