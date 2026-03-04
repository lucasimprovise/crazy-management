"""
MapStats cog — statistiques détaillées par map.

Pour chaque map jouée en pracc ou officiel, affiche :
  - Nombre de matchs, W/L, winrate
  - Rounds gagnés / perdus totaux + ratio
  - Série en cours (win streak / loss streak)
  - Tri par winrate décroissant

Commands:
  /mapstats show [team_name]
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from database import get_session, Team, Event, EventType, MatchResult
from utils.cog_helpers import get_team_for_command
from utils.i18n import t
from config import config

logger = logging.getLogger(__name__)


def _build_bar(winrate: int, width: int = 10) -> str:
    """Simple ASCII progress bar. 60% → ██████░░░░"""
    filled = round(winrate / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _color_for_wr(wr: int) -> int:
    """Embed color based on winrate."""
    if wr >= 60:
        return 0x00C17C   # Green
    if wr >= 45:
        return 0xFF4655   # Valorant red (neutral-ish)
    return 0xBD3944       # Deep red


class MapStatsCog(commands.Cog, name="MapStats"):
    """Statistiques par map."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def team_autocomplete(self, interaction: discord.Interaction, current: str):
        async for session in get_session():
            r = await session.execute(select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True))
            return [
                app_commands.Choice(name=obj.name + (f" [{obj.tag}]" if obj.tag else ""), value=obj.name)
                for obj in r.scalars().all() if current.lower() in obj.name.lower()
            ][:25]

    mapstats = app_commands.Group(name="mapstats", description="Map statistics / Stats par map")

    @mapstats.command(name="show", description="Map stats (W/L, winrate, rounds) · Stats par map")
    @app_commands.describe(
        team_name="Team · Équipe (optional)",
        event_type="Filter by type · Filtrer par type",
    )
    @app_commands.choices(event_type=[
        app_commands.Choice(name="🥊 Praccs only · Praccs uniquement", value="pracc"),
        app_commands.Choice(name="🏆 Officials only · Officiels uniquement", value="official"),
        app_commands.Choice(name="📊 All · Tous", value="all"),
    ])
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def show(
        self,
        interaction: discord.Interaction,
        team_name: Optional[str] = None,
        event_type: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await interaction.response.defer()

        filter_type = event_type.value if event_type else "all"

        async for session in get_session():
            team = await get_team_for_command(session, interaction, team_name)
            if not team:
                return

            # Fetch all events with a result
            query = select(Event).where(
                Event.team_id == team.id,
                Event.result != MatchResult.PENDING,
                Event.map_played != None,
            ).order_by(Event.scheduled_at)

            if filter_type == "pracc":
                query = query.where(Event.event_type == EventType.PRACC)
            elif filter_type == "official":
                query = query.where(Event.event_type == EventType.OFFICIAL)

            result = await session.execute(query)
            events = result.scalars().all()

            if not events:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title=t("mapstats.title", interaction, team=team.name),
                        description=t("mapstats.no_data", interaction),
                        color=0xFF4655,
                    )
                )
                return

            # ── Build per-map stats ───────────────────────────────────────────
            map_data: dict[str, dict] = defaultdict(lambda: {
                "wins": 0, "losses": 0, "draws": 0,
                "rounds_won": 0, "rounds_lost": 0,
                "history": [],   # list of "W"/"L"/"D" in chrono order
            })

            for ev in events:
                map_name = ev.map_played
                d = map_data[map_name]

                if ev.result == MatchResult.WIN:
                    d["wins"] += 1
                    d["history"].append("W")
                elif ev.result == MatchResult.LOSS:
                    d["losses"] += 1
                    d["history"].append("L")
                else:
                    d["draws"] += 1
                    d["history"].append("D")

                if ev.rounds_won is not None:
                    d["rounds_won"]  += ev.rounds_won
                if ev.rounds_lost is not None:
                    d["rounds_lost"] += ev.rounds_lost

            # ── Sort by winrate desc ──────────────────────────────────────────
            def winrate(d: dict) -> float:
                total = d["wins"] + d["losses"]
                return d["wins"] / total if total else 0.0

            sorted_maps = sorted(map_data.items(), key=lambda x: winrate(x[1]), reverse=True)
            total_events = len(events)

            # ── Build embed ───────────────────────────────────────────────────
            # Dominant winrate for embed color (weighted average)
            overall_wins   = sum(d["wins"]   for _, d in sorted_maps)
            overall_losses = sum(d["losses"] for _, d in sorted_maps)
            overall_total  = overall_wins + overall_losses
            overall_wr     = round(overall_wins / overall_total * 100) if overall_total else 0

            embed = discord.Embed(
                title=t("mapstats.title", interaction, team=team.name),
                description=t("mapstats.description", interaction, total=total_events),
                color=_color_for_wr(overall_wr),
            )

            # Overall summary line
            embed.add_field(
                name="📊 Overall",
                value=(
                    f"**{overall_wins}W / {overall_losses}L**  ·  "
                    f"`{overall_wr}%` winrate  ·  "
                    f"{_build_bar(overall_wr)}"
                ),
                inline=False,
            )
            embed.add_field(name="\u200b", value="\u200b", inline=False)  # spacer

            # Per-map fields
            for map_name, d in sorted_maps:
                total_map   = d["wins"] + d["losses"]
                wr          = round(d["wins"] / total_map * 100) if total_map else 0
                bar         = _build_bar(wr)

                rounds_line = ""
                if d["rounds_won"] or d["rounds_lost"]:
                    total_rounds = d["rounds_won"] + d["rounds_lost"]
                    rratio = round(d["rounds_won"] / total_rounds * 100) if total_rounds else 0
                    rounds_line = (
                        f"\n{t('mapstats.rounds_label', interaction)}: "
                        f"`{d['rounds_won']}–{d['rounds_lost']}`  (`{rratio}%`)"
                    )

                # Current streak
                streak_line = ""
                if d["history"]:
                    last = d["history"][-1]
                    streak = 1
                    for r in reversed(d["history"][:-1]):
                        if r == last:
                            streak += 1
                        else:
                            break
                    if streak >= 2:
                        if last == "W":
                            streak_line = f"\n{t('mapstats.streak_win', interaction, n=streak)}"
                        elif last == "L":
                            streak_line = f"\n{t('mapstats.streak_loss', interaction, n=streak)}"

                # Last 5 results mini-timeline
                icons = {"W": "🟢", "L": "🔴", "D": "🟡"}
                last5 = " ".join(icons.get(r, "⚪") for r in d["history"][-5:])

                embed.add_field(
                    name=f"🗺️  {map_name}",
                    value=(
                        f"**{d['wins']}W / {d['losses']}L**  ·  `{wr}%`\n"
                        f"{bar}{rounds_line}{streak_line}\n"
                        f"Last {min(5, len(d['history']))}: {last5}"
                    ),
                    inline=True,
                )

            embed.set_footer(text=t("mapstats.footer", interaction, bot_name=config.bot_name))
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MapStatsCog(bot))
