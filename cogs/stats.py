"""
Stats cog — player stats via Henrik Dev Valorant API + internal performance history.

Commands:
  /stats player <ign> <tag> [region]  — fetch live Valorant stats
  /stats perfs <member>               — performance history (in-bot DB)
  /stats leaderboard                  — team ACS leaderboard
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config import config
from database import get_session, Team, TeamRole, Player, PlayerPerformance
from utils.cog_helpers import get_team_for_command
from utils import success, error, warning, stats_embed, performance_embed
from utils.i18n import t
from utils.valorant_api import ValorantClient, ValorantAPIError

logger = logging.getLogger(__name__)

REGION_CHOICES = [
    app_commands.Choice(name="Europe (EU)", value="eu"),
    app_commands.Choice(name="North America (NA)", value="na"),
    app_commands.Choice(name="Asia Pacific (AP)", value="ap"),
    app_commands.Choice(name="Korea (KR)", value="kr"),
    app_commands.Choice(name="Latin America (LATAM)", value="latam"),
    app_commands.Choice(name="Brazil (BR)", value="br"),
]


class StatsCog(commands.Cog, name="Stats"):
    """Statistiques joueurs Valorant."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_team(self, session, guild_id: int) -> Team | None:
        r = await session.execute(select(Team).where(Team.guild_id == guild_id))
        return r.scalar_one_or_none()

    # ── Group ─────────────────────────────────────────────────────────────────

    stats_group = app_commands.Group(name="stats", description="Statistiques Valorant")

    # /stats player ─────────────────────────────────────────────────────────────

    @stats_group.command(name="player", description="Stats live d'un joueur via l'API Valorant")
    @app_commands.describe(
        ign="Riot ID (ex: TenZ)",
        tag="Riot Tag (ex: NA1)",
        region="Région du serveur",
    )
    @app_commands.choices(region=REGION_CHOICES)
    async def player(
        self,
        interaction: discord.Interaction,
        ign: str,
        tag: str,
        region: app_commands.Choice[str] = None,
    ) -> None:
        await interaction.response.defer()

        if not config.is_henrik_configured:
            await interaction.followup.send(
                embed=warning(
                    t("stats.not_configured_title", interaction),
                    "Ajoutez `HENRIK_API_KEY=votre_clé` dans `.env`.\n"
                    "Obtenez une clé gratuite sur https://docs.henrikdev.xyz",
                )
            )
            return

        region_val = region.value if region else "eu"

        try:
            async with ValorantClient(config.henrik_api_key) as client:
                data = await client.get_player_stats(region_val, ign, tag)

            embed = stats_embed(f"{ign}#{tag}", data)
            await interaction.followup.send(embed=embed)

        except ValorantAPIError as e:
            await interaction.followup.send(embed=error("Erreur API", str(e)))
        except Exception as e:
            logger.exception("Erreur stats player")
            await interaction.followup.send(embed=error("Erreur inattendue", str(e)[:300]))

    # /stats perfs ──────────────────────────────────────────────────────────────

    @stats_group.command(name="perfs", description="Historique des performances enregistrées d'un joueur")
    @app_commands.describe(member="Membre Discord (optionnel, soi-même par défaut)")
    async def perfs(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        await interaction.response.defer()
        target = member or interaction.user

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            player_r = await session.execute(
                select(Player).where(Player.discord_id == target.id, Player.team_id == team.id)
            )
            player = player_r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=warning("Joueur introuvable", f"{target.mention} n'est pas dans le roster.")
                )
                return

            perf_r = await session.execute(
                select(PlayerPerformance)
                .where(PlayerPerformance.player_id == player.id)
                .options(selectinload(PlayerPerformance.event))
                .order_by(PlayerPerformance.id.desc())
                .limit(10)
            )
            perfs = perf_r.scalars().all()

            await interaction.followup.send(embed=performance_embed(player.ign, list(reversed(perfs))))

    # /stats leaderboard ────────────────────────────────────────────────────────

    @stats_group.command(name="leaderboard", description="Classement ACS de l'équipe (praccs internes)")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            players_r = await session.execute(
                select(Player).where(Player.team_id == team.id, Player.is_active == True)
            )
            players = players_r.scalars().all()

            leaderboard_data = []
            for player in players:
                perf_r = await session.execute(
                    select(PlayerPerformance).where(
                        PlayerPerformance.player_id == player.id,
                        PlayerPerformance.acs != None,
                    )
                )
                perfs = perf_r.scalars().all()
                if perfs:
                    avg_acs = sum(p.acs for p in perfs if p.acs) / len(perfs)
                    avg_kd = sum(
                        (p.kills or 0) / max(p.deaths or 1, 1) for p in perfs
                    ) / len(perfs)
                    leaderboard_data.append({
                        "ign": player.ign,
                        "acs": avg_acs,
                        "kd": avg_kd,
                        "matches": len(perfs),
                        "role": player.role or "N/A",
                    })

            leaderboard_data.sort(key=lambda x: x["acs"], reverse=True)

            embed = discord.Embed(
                title=f"🏆 Leaderboard ACS — {team.name}",
                color=0xFF4655,
                description="Classement basé sur les praccs enregistrées\n\u200b",
            )

            medals = ["🥇", "🥈", "🥉"]
            for i, entry in enumerate(leaderboard_data):
                medal = medals[i] if i < 3 else f"`#{i+1}`"
                embed.add_field(
                    name=f"{medal} {entry['ign']}",
                    value=(
                        f"ACS : `{entry['acs']:.0f}` · K/D : `{entry['kd']:.2f}`\n"
                        f"Rôle : `{entry['role']}` · {entry['matches']} match(s)"
                    ),
                    inline=False,
                )

            if not leaderboard_data:
                embed.description = "Aucune performance enregistrée. Utilisez `/pracc perf`."

            embed.set_footer(text=config.bot_name)
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
