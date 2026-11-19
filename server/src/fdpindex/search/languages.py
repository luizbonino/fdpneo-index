"""The index's language fallback order (TASKS 11.8).

Titles, descriptions and labels come in several languages. Which one a reader
sees: the reader's language, then untagged text, then this index's fallback
languages in order, then any. The order is a search setting
(``languageFallback``, default English only); the settings service updates it
here whenever the settings are read or changed, so the pickers need not be
handed it on every call.
"""

from __future__ import annotations

_fallback: tuple[str, ...] = ("en",)


def fallback() -> tuple[str, ...]:
    return _fallback


def set_fallback(languages: list[str] | tuple[str, ...]) -> None:
    global _fallback
    _fallback = tuple(dict.fromkeys(lang.lower() for lang in languages)) or ("en",)
