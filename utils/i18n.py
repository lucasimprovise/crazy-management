"""
i18n — Internationalisation engine.

Discord fournit la locale de l'utilisateur via interaction.locale.
On supporte fr (français) et en (anglais, fallback par défaut).

Usage dans un cog :
    from utils.i18n import t

    # Avec une interaction Discord (détection auto de la langue)
    msg = t("roster.add_success_title", interaction)

    # Avec substitutions
    msg = t("team.name_taken", interaction, name="TeamName")

    # Sans interaction (langue par défaut)
    msg = t("general.unexpected_error")
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import discord

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_SUPPORTED    = {"fr", "en"}
_DEFAULT_LANG = "en"

# Discord locale codes → our lang codes
_DISCORD_LOCALE_MAP = {
    "fr":    "fr",
    "fr-FR": "fr",
    "en-US": "en",
    "en-GB": "en",
}


@lru_cache(maxsize=None)
def _load(lang: str) -> dict:
    """Load and cache a locale JSON file."""
    path = _LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        logger.warning(f"Locale file not found: {path}. Falling back to {_DEFAULT_LANG}.")
        path = _LOCALES_DIR / f"{_DEFAULT_LANG}.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_lang(interaction: discord.Interaction | None) -> str:
    """Resolve the language from a Discord interaction locale."""
    if interaction is None:
        return _DEFAULT_LANG
    locale = str(getattr(interaction, "locale", "") or "")
    return _DISCORD_LOCALE_MAP.get(locale, _DEFAULT_LANG)


def _get_nested(data: dict, key: str) -> str | Any:
    """Traverse dot-notated key into nested dict. Returns key string if missing."""
    parts = key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return key  # Missing key → return the key itself as fallback
        current = current[part]
    return current


def t(
    key: str,
    interaction: discord.Interaction | None = None,
    lang: str | None = None,
    **kwargs: Any,
) -> str:
    """
    Translate a dot-notated key.

    Priority: explicit lang > interaction locale > default lang.

    Args:
        key:         Dot-notated translation key, e.g. "roster.add_success_title"
        interaction: Discord Interaction (used to detect user locale)
        lang:        Explicit language override ("fr" or "en")
        **kwargs:    Format substitutions, e.g. name="TeamName"

    Returns:
        Translated and formatted string.
    """
    resolved_lang = lang or _resolve_lang(interaction)

    # Try resolved lang first, then fallback to default
    data = _load(resolved_lang)
    value = _get_nested(data, key)

    # If not found in resolved lang, try default
    if value == key and resolved_lang != _DEFAULT_LANG:
        data = _load(_DEFAULT_LANG)
        value = _get_nested(data, key)

    if not isinstance(value, str):
        return key  # key points to a dict, not a leaf string

    if kwargs:
        try:
            value = value.format(**kwargs)
        except KeyError as e:
            logger.warning(f"i18n format error for key '{key}': missing {e}")

    return value


def tlist(
    key: str,
    interaction: discord.Interaction | None = None,
    lang: str | None = None,
) -> list:
    """Get a translated list (e.g. day names, month names)."""
    resolved_lang = lang or _resolve_lang(interaction)
    data = _load(resolved_lang)
    value = _get_nested(data, key)
    if not isinstance(value, list):
        data = _load(_DEFAULT_LANG)
        value = _get_nested(data, key)
    return value if isinstance(value, list) else []


def tdict(
    key: str,
    interaction: discord.Interaction | None = None,
    lang: str | None = None,
) -> dict:
    """Get a translated dict (e.g. slot labels, event type labels)."""
    resolved_lang = lang or _resolve_lang(interaction)
    data = _load(resolved_lang)
    value = _get_nested(data, key)
    if not isinstance(value, dict):
        data = _load(_DEFAULT_LANG)
        value = _get_nested(data, key)
    return value if isinstance(value, dict) else {}
