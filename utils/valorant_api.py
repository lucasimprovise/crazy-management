"""
Valorant stats client using the Henrik Dev unofficial API.
Documentation: https://docs.henrikdev.xyz
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

HENRIK_BASE = "https://api.henrikdev.xyz/valorant"


class ValorantAPIError(Exception):
    pass


class ValorantClient:
    """Async wrapper around Henrik Dev Valorant API."""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "ValorantClient":
        headers = {"Authorization": self.api_key} if self.api_key else {}
        self._session = aiohttp.ClientSession(headers=headers)
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()

    async def _get(self, endpoint: str) -> dict:
        if not self._session:
            raise RuntimeError("Utilisez ValorantClient comme context manager.")
        url = f"{HENRIK_BASE}{endpoint}"
        async with self._session.get(url) as resp:
            if resp.status == 429:
                raise ValorantAPIError("Rate limit atteint. Ajoutez une clé API Henrik.")
            if resp.status == 404:
                raise ValorantAPIError("Joueur introuvable.")
            if resp.status != 200:
                raise ValorantAPIError(f"Erreur API Henrik : {resp.status}")
            return await resp.json()

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_account(self, name: str, tag: str) -> dict:
        """Fetch basic account info."""
        data = await self._get(f"/v1/account/{name}/{tag}")
        return data.get("data", {})

    # ── MMR / Rank ────────────────────────────────────────────────────────────

    async def get_mmr(self, region: str, name: str, tag: str) -> dict:
        """Fetch current rank and MMR."""
        data = await self._get(f"/v2/mmr/{region}/{name}/{tag}")
        return data.get("data", {})

    # ── Match history ─────────────────────────────────────────────────────────

    async def get_match_history(
        self, region: str, name: str, tag: str, mode: str = "competitive", size: int = 10
    ) -> list[dict]:
        """Fetch recent match history."""
        data = await self._get(f"/v3/matches/{region}/{name}/{tag}?mode={mode}&size={size}")
        return data.get("data", [])

    # ── Aggregated stats ──────────────────────────────────────────────────────

    async def get_player_stats(self, region: str, name: str, tag: str) -> dict:
        """
        Build an aggregated stats dict from account + MMR + recent matches.
        Returns a normalised dict ready to be used by embeds.stats_embed().
        """
        result: dict = {
            "account": {},
            "stats": {
                "rank": "N/A",
                "acs": "N/A",
                "kda": "N/A",
                "hs_percent": "N/A",
                "winrate": "N/A",
                "matches": 0,
                "top_agents": [],
            },
        }

        try:
            account = await self.get_account(name, tag)
            result["account"] = account
        except ValorantAPIError as e:
            logger.warning(f"Henrik API account error: {e}")
            return result

        try:
            mmr = await self.get_mmr(region, name, tag)
            current = mmr.get("current_data", {})
            result["stats"]["rank"] = (
                f"{current.get('currenttierpatched', 'N/A')} "
                f"({current.get('ranking_in_tier', 0)} RR)"
            )
        except ValorantAPIError as e:
            logger.warning(f"Henrik API MMR error: {e}")

        try:
            matches = await self.get_match_history(region, name, tag, size=20)
            if matches:
                result["stats"].update(self._compute_stats(matches, name, tag))
        except ValorantAPIError as e:
            logger.warning(f"Henrik API match history error: {e}")

        return result

    def _compute_stats(self, matches: list[dict], name: str, tag: str) -> dict:
        """Compute aggregate stats from match list."""
        kills_total = deaths_total = assists_total = 0
        acs_total = hs_total = wins = 0
        agents: dict[str, int] = {}
        count = 0

        for match in matches:
            players = match.get("players", {}).get("all_players", [])
            player_data = next(
                (
                    p for p in players
                    if p.get("name", "").lower() == name.lower()
                    and p.get("tag", "").lower() == tag.lower()
                ),
                None,
            )
            if not player_data:
                continue

            stats = player_data.get("stats", {})
            kills_total  += stats.get("kills", 0)
            deaths_total += stats.get("deaths", 1)
            assists_total += stats.get("assists", 0)
            acs_total    += stats.get("score", 0) // max(match.get("metadata", {}).get("rounds_played", 1), 1)
            hs_total     += stats.get("headshots", 0)

            agent = player_data.get("character", "")
            agents[agent] = agents.get(agent, 0) + 1

            # Win check
            my_team = player_data.get("team", "").lower()
            teams = match.get("teams", {})
            team_data = teams.get(my_team, {})
            if team_data.get("has_won"):
                wins += 1

            count += 1

        if count == 0:
            return {}

        top_agents = sorted(agents, key=lambda a: agents[a], reverse=True)[:3]
        total_shots = kills_total + hs_total or 1

        return {
            "acs": round(acs_total / count),
            "kda": f"{kills_total/count:.1f}/{deaths_total/count:.1f}/{assists_total/count:.1f}",
            "hs_percent": round(hs_total / total_shots * 100),
            "winrate": round(wins / count * 100),
            "matches": count,
            "top_agents": top_agents,
        }
