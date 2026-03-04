"""
pracc.com scraper / API client.

pracc.com ne fournit pas d'API publique officielle.
Ce module implémente deux approches :
  1. Scraping HTML via aiohttp + BeautifulSoup (session authentifiée)
  2. Interception des endpoints XHR internes utilisés par l'app web

⚠️  Utiliser uniquement avec vos propres credentials.
    Respectez les CGU de pracc.com.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pracc.com"
LOGIN_URL = f"{BASE_URL}/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
API_MATCHES_URL = f"{BASE_URL}/api/matches"  # XHR endpoint (peut évoluer)


@dataclass
class PraccMatch:
    """Represents a scrim match from pracc.com."""
    pracc_id: str
    opponent: str
    scheduled_at: datetime
    map_name: Optional[str] = None
    status: str = "pending"          # pending | confirmed | cancelled
    server: Optional[str] = None
    notes: Optional[str] = None
    raw: dict = field(default_factory=dict)


class PraccClient:
    """
    Async client to interact with pracc.com.

    Usage:
        async with PraccClient(email, password) as client:
            if await client.login():
                matches = await client.get_upcoming_matches()
    """

    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self._session: aiohttp.ClientSession | None = None
        self._authenticated = False

    async def __aenter__(self) -> "PraccClient":
        self._session = aiohttp.ClientSession(
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            }
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()

    # ── Authentication ────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """
        Authenticate against pracc.com.
        Returns True if login was successful.
        """
        if not self._session:
            raise RuntimeError("Use as async context manager.")

        try:
            # Step 1: GET login page to grab CSRF token
            async with self._session.get(LOGIN_URL) as resp:
                html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")
            csrf = self._extract_csrf(soup)

            # Step 2: POST credentials
            payload = {
                "email": self.email,
                "password": self.password,
                "_token": csrf,
            }
            async with self._session.post(LOGIN_URL, data=payload, allow_redirects=True) as resp:
                if resp.status in (200, 302):
                    # Verify login by checking dashboard accessibility
                    page_text = await resp.text()
                    self._authenticated = "dashboard" in str(resp.url) or "logout" in page_text
                    if self._authenticated:
                        logger.info("pracc.com: Authentification réussie.")
                    else:
                        logger.warning("pracc.com: Authentification échouée (credentials incorrects ?).")
                    return self._authenticated

        except aiohttp.ClientError as e:
            logger.error(f"pracc.com: Erreur réseau lors du login : {e}")

        return False

    # ── Data fetching ─────────────────────────────────────────────────────────

    async def get_upcoming_matches(self) -> list[PraccMatch]:
        """Fetch upcoming confirmed/pending scrim matches."""
        if not self._authenticated:
            raise RuntimeError("Non authentifié. Appelez login() d'abord.")

        matches: list[PraccMatch] = []

        # Try JSON API endpoint first (faster)
        json_matches = await self._fetch_matches_json()
        if json_matches is not None:
            return json_matches

        # Fallback: HTML scraping
        return await self._scrape_dashboard_matches()

    async def _fetch_matches_json(self) -> list[PraccMatch] | None:
        """
        Attempt to hit the internal JSON API.
        pracc.com uses React/Inertia.js — shared data is often in a __page prop.
        """
        try:
            async with self._session.get(
                DASHBOARD_URL,
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            ) as resp:
                if resp.content_type == "application/json":
                    data = await resp.json()
                    return self._parse_json_matches(data)
        except Exception as e:
            logger.debug(f"pracc.com: JSON endpoint non disponible : {e}")
        return None

    async def _scrape_dashboard_matches(self) -> list[PraccMatch]:
        """HTML scraping fallback."""
        matches: list[PraccMatch] = []
        try:
            async with self._session.get(DASHBOARD_URL) as resp:
                html = await resp.text()

            soup = BeautifulSoup(html, "html.parser")

            # pracc.com embeds Inertia page props as JSON in a <div id="app">
            app_div = soup.find("div", {"id": "app"})
            if app_div and app_div.get("data-page"):
                import json
                page_data = json.loads(app_div["data-page"])
                props = page_data.get("props", {})
                raw_matches = (
                    props.get("matches", [])
                    or props.get("upcomingMatches", [])
                    or props.get("scrims", [])
                )
                for raw in raw_matches:
                    match = self._parse_single_match(raw)
                    if match:
                        matches.append(match)
                return matches

            # Fallback: manual HTML parsing of match cards
            match_cards = soup.select(".match-card, .scrim-item, [data-match-id]")
            for card in match_cards:
                match = self._parse_html_card(card)
                if match:
                    matches.append(match)

        except Exception as e:
            logger.error(f"pracc.com: Erreur scraping : {e}")

        return matches

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_json_matches(self, data: dict) -> list[PraccMatch]:
        matches = []
        raw_list = data.get("data", data.get("matches", []))
        for raw in raw_list:
            match = self._parse_single_match(raw)
            if match:
                matches.append(match)
        return matches

    def _parse_single_match(self, raw: dict) -> PraccMatch | None:
        try:
            scheduled_raw = raw.get("scheduled_at") or raw.get("date") or raw.get("time")
            scheduled_at = (
                datetime.fromisoformat(scheduled_raw) if scheduled_raw
                else datetime.utcnow()
            )
            return PraccMatch(
                pracc_id=str(raw.get("id", "")),
                opponent=raw.get("opponent", {}).get("name", "") or raw.get("opponent_name", "Unknown"),
                scheduled_at=scheduled_at,
                map_name=raw.get("map", {}).get("name") if isinstance(raw.get("map"), dict) else raw.get("map"),
                status=raw.get("status", "pending"),
                server=raw.get("server") or raw.get("connect_string"),
                notes=raw.get("notes") or raw.get("message"),
                raw=raw,
            )
        except Exception as e:
            logger.warning(f"pracc.com: Impossible de parser le match : {e}")
            return None

    def _parse_html_card(self, card) -> PraccMatch | None:
        """Parse a single HTML match card (best-effort)."""
        try:
            match_id = card.get("data-match-id", "unknown")
            opponent_el = card.select_one(".opponent-name, .team-name, [class*='opponent']")
            date_el = card.select_one("time, [data-timestamp], [class*='date']")

            opponent = opponent_el.get_text(strip=True) if opponent_el else "Unknown"
            ts = date_el.get("datetime") or date_el.get("data-timestamp") if date_el else None
            scheduled_at = datetime.fromisoformat(ts) if ts else datetime.utcnow()

            return PraccMatch(
                pracc_id=str(match_id),
                opponent=opponent,
                scheduled_at=scheduled_at,
            )
        except Exception:
            return None

    @staticmethod
    def _extract_csrf(soup: BeautifulSoup) -> str:
        meta = soup.find("meta", {"name": "csrf-token"})
        if meta:
            return meta.get("content", "")
        token_input = soup.find("input", {"name": "_token"})
        return token_input.get("value", "") if token_input else ""
