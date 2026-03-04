"""
Pracc cog — scrim management + optional pracc.com sync.

Commands:
  /pracc history [limit]         — historique des praccs
  /pracc add <opponent> <date> <time> [map]  — ajouter une pracc manuellement
  /pracc perf <event_id> <member> <agent> <k> <d> <a> [acs] [hs]
  /pracc sync                    — synchro depuis pracc.com (si configuré)
  /pracc stats                   — stats win/loss globales
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from config import config
from database import get_session, Team, TeamRole, Player, Event, PlayerPerformance, EventType, MatchResult
from utils.cog_helpers import get_team_for_command
from utils import success, error, warning, pracc_summary_embed, performance_embed
from utils.i18n import t
from utils.scraper import PraccClient

logger = logging.getLogger(__name__)

VALORANT_MAPS = [
    "Ascent", "Bind", "Breeze", "Fracture", "Haven",
    "Icebox", "Lotus", "Pearl", "Split", "Sunset",
]

VALORANT_AGENTS = [
    "Astra", "Breach", "Brimstone", "Chamber", "Clove",
    "Cypher", "Deadlock", "Fade", "Gekko", "Harbor",
    "Iso", "Jett", "KAY/O", "Killjoy", "Neon",
    "Omen", "Phoenix", "Raze", "Reyna", "Sage",
    "Skye", "Sova", "Tejo", "Viper", "Vyse", "Yoru",
]


class PraccCog(commands.Cog, name="Praccs"):
    """Gestion des praccs / scrims."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_team(self, session, guild_id: int) -> Team | None:
        r = await session.execute(select(Team).where(Team.guild_id == guild_id))
        return r.scalar_one_or_none()

    # ── Autocompletes ─────────────────────────────────────────────────────────

    async def map_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=m, value=m)
            for m in VALORANT_MAPS
            if current.lower() in m.lower()
        ]

    async def agent_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=a, value=a)
            for a in VALORANT_AGENTS
            if current.lower() in a.lower()
        ][:25]

    # ── Group ─────────────────────────────────────────────────────────────────

    pracc = app_commands.Group(name="pracc", description="Gestion des praccs / scrims")

    # /pracc add ────────────────────────────────────────────────────────────────

    @pracc.command(name="add", description="Ajouter une pracc manuellement")
    @app_commands.describe(
        opponent="Équipe adverse",
        date="Date (JJ/MM/AAAA)",
        time="Heure (HH:MM)",
        map_name="Map jouée",
        result="Résultat immédiat (optionnel)",
        rounds_won="Rounds gagnés",
        rounds_lost="Rounds perdus",
    )
    @app_commands.choices(
        result=[
            app_commands.Choice(name="🟢 Victoire", value="win"),
            app_commands.Choice(name="🔴 Défaite", value="loss"),
            app_commands.Choice(name="🟡 Nul", value="draw"),
        ]
    )
    @app_commands.autocomplete(map_name=map_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        opponent: str,
        date: str,
        time: str,
        map_name: Optional[str] = None,
        result: Optional[app_commands.Choice[str]] = None,
        rounds_won: Optional[int] = None,
        rounds_lost: Optional[int] = None,
    ) -> None:
        await interaction.response.defer()

        try:
            scheduled_at = datetime.strptime(f"{date} {time}", "%d/%m/%Y %H:%M")
        except ValueError:
            await interaction.followup.send(
                embed=error("Format invalide", "Date: JJ/MM/AAAA, Heure: HH:MM")
            )
            return

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            event = Event(
                team_id=team.id,
                event_type=EventType.PRACC,
                title=f"Pracc vs {opponent}",
                scheduled_at=scheduled_at,
                opponent=opponent,
                map_played=map_name,
                result=MatchResult(result.value) if result else MatchResult.PENDING,
                rounds_won=rounds_won,
                rounds_lost=rounds_lost,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)

            score_str = ""
            if rounds_won is not None and rounds_lost is not None:
                score_str = f" · Score : **{rounds_won}–{rounds_lost}**"

            embed = discord.Embed(
                title="🥊 Pracc enregistrée !",
                description=f"vs **{opponent}**{score_str}",
                color=0xFF4655,
            )
            if map_name:
                embed.add_field(name="🗺️ Map", value=map_name, inline=True)
            ts = int(scheduled_at.timestamp())
            embed.add_field(name="📅 Date", value=f"<t:{ts}:F>", inline=True)
            embed.add_field(name="🆔 ID", value=f"`{event.id}`", inline=True)
            embed.set_footer(text="Utilisez /pracc perf pour ajouter les perfs individuelles")
            await interaction.followup.send(embed=embed)

    # /pracc history ────────────────────────────────────────────────────────────

    @pracc.command(name="history", description="Historique des praccs de l'équipe")
    @app_commands.describe(limit="Nombre de praccs à afficher (défaut: 10)")
    async def history(self, interaction: discord.Interaction, limit: int = 10) -> None:
        await interaction.response.defer()
        limit = max(1, min(limit, 25))

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            result = await session.execute(
                select(Event)
                .where(Event.team_id == team.id, Event.event_type == EventType.PRACC)
                .order_by(Event.scheduled_at.desc())
                .limit(limit)
            )
            events = result.scalars().all()

            await interaction.followup.send(
                embed=pracc_summary_embed(team.name, list(reversed(events)))
                if events
                else discord.Embed(
                    title="🥊 Aucune pracc",
                    description="Aucune pracc enregistrée. Utilisez `/pracc add` ou `/pracc sync`.",
                    color=0xFF4655,
                )
            )

    # /pracc stats ──────────────────────────────────────────────────────────────

    @pracc.command(name="stats", description="Statistiques globales praccs (W/L, maps...)")
    async def stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            result = await session.execute(
                select(Event).where(
                    Event.team_id == team.id,
                    Event.event_type == EventType.PRACC,
                    Event.result != MatchResult.PENDING,
                )
            )
            events = result.scalars().all()

            if not events:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="📊 Aucune stat",
                        description="Aucun résultat enregistré pour les praccs.",
                        color=0xFF4655,
                    )
                )
                return

            wins = sum(1 for e in events if e.result == MatchResult.WIN)
            losses = sum(1 for e in events if e.result == MatchResult.LOSS)
            draws = sum(1 for e in events if e.result == MatchResult.DRAW)
            total = len(events)
            wr = round(wins / total * 100) if total else 0

            # Map winrates
            map_stats: dict[str, dict[str, int]] = {}
            for e in events:
                m = e.map_played or "Unknown"
                if m not in map_stats:
                    map_stats[m] = {"w": 0, "l": 0}
                if e.result == MatchResult.WIN:
                    map_stats[m]["w"] += 1
                elif e.result == MatchResult.LOSS:
                    map_stats[m]["l"] += 1

            embed = discord.Embed(
                title=f"📊 Stats Praccs — {team.name}",
                color=0xFF4655,
            )
            embed.description = (
                f"**{wins}W / {losses}L / {draws}D** · `{wr}%` winrate\n"
                f"Total : **{total}** pracc(s)\n\u200b"
            )

            if map_stats:
                map_lines = []
                for map_name, s in sorted(map_stats.items()):
                    map_total = s["w"] + s["l"]
                    map_wr = round(s["w"] / map_total * 100) if map_total else 0
                    bar = "█" * (map_wr // 10) + "░" * (10 - map_wr // 10)
                    map_lines.append(f"`{map_name:<10}` {bar} `{map_wr}%` ({s['w']}W/{s['l']}L)")
                embed.add_field(name="🗺️ Winrate par map", value="\n".join(map_lines), inline=False)

            embed.set_footer(text=config.bot_name)
            await interaction.followup.send(embed=embed)

    # /pracc perf ───────────────────────────────────────────────────────────────

    @pracc.command(name="perf", description="Enregistrer les stats d'un joueur pour une pracc")
    @app_commands.describe(
        event_id="ID de l'événement",
        member="Joueur Discord",
        agent="Agent joué",
        kills="Kills",
        deaths="Deaths",
        assists="Assists",
        acs="Average Combat Score (optionnel)",
        hs_percent="Headshot % (optionnel)",
        adr="Average Damage per Round (optionnel)",
    )
    @app_commands.autocomplete(agent=agent_autocomplete)
    async def perf(
        self,
        interaction: discord.Interaction,
        event_id: int,
        member: discord.Member,
        agent: str,
        kills: int,
        deaths: int,
        assists: int,
        acs: Optional[int] = None,
        hs_percent: Optional[int] = None,
        adr: Optional[int] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            # Get event
            event_r = await session.execute(
                select(Event).where(Event.id == event_id, Event.team_id == team.id)
            )
            event = event_r.scalar_one_or_none()
            if not event:
                await interaction.followup.send(
                    embed=error("Événement introuvable", f"ID `{event_id}` non trouvé."),
                    ephemeral=True,
                )
                return

            # Get player
            player_r = await session.execute(
                select(Player).where(Player.discord_id == member.id, Player.team_id == team.id)
            )
            player = player_r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=error("Joueur introuvable", f"{member.mention} n'est pas dans le roster."),
                    ephemeral=True,
                )
                return

            perf = PlayerPerformance(
                event_id=event.id,
                player_id=player.id,
                agent=agent,
                kills=kills,
                deaths=deaths,
                assists=assists,
                acs=acs,
                hs_percent=hs_percent,
                adr=adr,
            )
            session.add(perf)
            await session.commit()

            kd = kills / max(deaths, 1)
            await interaction.followup.send(
                embed=success(
                    t("pracc.perf_success_title", interaction),
                    f"**{player.ign}** · {agent}\n"
                    f"K/D/A : `{kills}/{deaths}/{assists}` · K/D : `{kd:.2f}`"
                    + (f" · ACS : `{acs}`" if acs else "")
                    + (f" · HS : `{hs_percent}%`" if hs_percent else ""),
                ),
                ephemeral=True,
            )

    # /pracc sync ───────────────────────────────────────────────────────────────

    @pracc.command(name="sync", description="Synchroniser les praccs depuis pracc.com")
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if not config.is_pracc_configured:
            await interaction.followup.send(
                embed=warning(
                    t("pracc.sync_not_configured_title", interaction),
                    "Pour activer la synchro, renseignez dans votre `.env` :\n"
                    "```\nPRACC_EMAIL=votre@email.com\n"
                    "PRACC_PASSWORD=votremdp\n"
                    "PRACC_SYNC_ENABLED=true\n```",
                ),
                ephemeral=True,
            )
            return

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

        await interaction.followup.send(
            embed=discord.Embed(title="⏳ Synchronisation en cours...", color=0xFF4655),
            ephemeral=True,
        )

        try:
            async with PraccClient(config.pracc_email, config.pracc_password) as client:
                logged_in = await client.login()
                if not logged_in:
                    await interaction.edit_original_response(
                        embed=error("Error", "Identifiants pracc.com incorrects.")
                    )
                    return

                matches = await client.get_upcoming_matches()

            if not matches:
                await interaction.edit_original_response(
                    embed=warning("Aucun match trouvé", "pracc.com n'a retourné aucune pracc à venir.")
                )
                return

            added = 0
            async for session in get_session():
                team = await self._get_team(session, interaction.guild_id)
                for m in matches:
                    # Skip already synced
                    existing = await session.execute(
                        select(Event).where(Event.pracc_id == m.pracc_id, Event.team_id == team.id)
                    )
                    if existing.scalar_one_or_none():
                        continue

                    event = Event(
                        team_id=team.id,
                        event_type=EventType.PRACC,
                        title=f"Pracc vs {m.opponent}",
                        scheduled_at=m.scheduled_at,
                        opponent=m.opponent,
                        map_played=m.map_name,
                        result=MatchResult.PENDING,
                        pracc_id=m.pracc_id,
                        notes=m.notes,
                    )
                    session.add(event)
                    added += 1
                await session.commit()

            await interaction.edit_original_response(
                embed=success(
                    t("pracc.sync_success_title", interaction),
                    f"**{added}** nouvelle(s) pracc(s) importée(s) depuis pracc.com.\n"
                    f"Total récupéré : {len(matches)} match(s).",
                )
            )

        except Exception as e:
            logger.exception("Erreur lors de la synchro pracc.com")
            await interaction.edit_original_response(
                embed=error(t("pracc.sync_error", interaction), f"```{str(e)[:300]}```")
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PraccCog(bot))
