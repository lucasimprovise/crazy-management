"""
Calendar cog — schedule and track team events (praccs, officials, meetings).

Commands:
  /cal add <title> <date> <time> <type> [opponent] [map]
  /cal list [month]
  /cal cancel <id>
  /cal result <id> <result> [score_win] [score_loss]
  /cal vod <id> <url>
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, and_, extract

from database import get_session, Team, TeamRole, Event, EventType, MatchResult
from utils.poster import post_event_added
from utils.cog_helpers import get_team_for_command
from utils import success, error, warning, event_embed, calendar_embed
from utils.i18n import t

logger = logging.getLogger(__name__)

TYPE_CHOICES = [
    app_commands.Choice(name="🥊 Pracc (scrim)", value="pracc"),
    app_commands.Choice(name="🏆 Match officiel", value="official"),
    app_commands.Choice(name="📋 Réunion", value="meeting"),
]

RESULT_CHOICES = [
    app_commands.Choice(name="🟢 Victoire", value="win"),
    app_commands.Choice(name="🔴 Défaite", value="loss"),
    app_commands.Choice(name="🟡 Nul", value="draw"),
]

VALORANT_MAPS = [
    "Ascent", "Bind", "Breeze", "Fracture", "Haven",
    "Icebox", "Lotus", "Pearl", "Split", "Sunset",
]

MONTH_FR = [
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]


class CalendarCog(commands.Cog, name="Calendrier"):
    """Calendrier des événements de l'équipe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_team(self, session, guild_id: int) -> Team | None:
        r = await session.execute(select(Team).where(Team.guild_id == guild_id))
        return r.scalar_one_or_none()

    # ── Map autocomplete ──────────────────────────────────────────────────────

    async def map_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=m, value=m)
            for m in VALORANT_MAPS
            if current.lower() in m.lower()
        ]

    # ── Group ─────────────────────────────────────────────────────────────────

    cal = app_commands.Group(name="cal", description="Calendrier de l'équipe")

    # /cal add ──────────────────────────────────────────────────────────────────

    @cal.command(name="add", description="Ajouter un événement au calendrier")
    @app_commands.describe(
        title="Titre de l'événement",
        date="Date (JJ/MM/AAAA)",
        time="Heure (HH:MM, ex: 20:00)",
        event_type="Type d'événement",
        opponent="Nom de l'adversaire (pour praccs/officials)",
        map_name="Map jouée",
        description="Description ou note",
    )
    @app_commands.choices(event_type=TYPE_CHOICES)
    @app_commands.autocomplete(map_name=map_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time: str,
        event_type: app_commands.Choice[str],
        opponent: Optional[str] = None,
        map_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            scheduled_at = datetime.strptime(f"{date} {time}", "%d/%m/%Y %H:%M")
        except ValueError:
            await interaction.followup.send(
                embed=error(t("general.invalid_datetime", interaction)),
                ephemeral=True,
            )
            return

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            event = Event(
                team_id=team.id,
                event_type=EventType(event_type.value),
                title=title,
                description=description,
                scheduled_at=scheduled_at,
                opponent=opponent,
                map_played=map_name,
                result=MatchResult.PENDING,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)

            # Visual feedback in #calendar
            if interaction.guild:
                await post_event_added(interaction.guild, session, team, event, interaction.user)

            await interaction.followup.send(
                embed=event_embed(event),
                ephemeral=False,
            )

    # /cal list ─────────────────────────────────────────────────────────────────

    @cal.command(name="list", description="Voir le calendrier (mois en cours ou spécifié)")
    @app_commands.describe(month="Numéro du mois (1–12)", year="Année (ex: 2025)")
    async def list_events(
        self,
        interaction: discord.Interaction,
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> None:
        await interaction.response.defer()
        now = datetime.utcnow()
        month = month or now.month
        year = year or now.year

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            result = await session.execute(
                select(Event)
                .where(
                    and_(
                        Event.team_id == team.id,
                        extract("month", Event.scheduled_at) == month,
                        extract("year", Event.scheduled_at) == year,
                    )
                )
                .order_by(Event.scheduled_at)
            )
            events = result.scalars().all()
            month_label = f"{MONTH_FR[month]} {year}"
            await interaction.followup.send(
                embed=calendar_embed(team.name, events, month_label)
                if events
                else discord.Embed(
                    title=f"🗓️ Calendrier — {month_label}",
                    description="Aucun événement ce mois-ci. Utilisez `/cal add` pour en créer un.",
                    color=0xFF4655,
                )
            )

    # /cal cancel ───────────────────────────────────────────────────────────────

    @cal.command(name="cancel", description="Annuler un événement")
    @app_commands.describe(event_id="ID de l'événement (visible dans les embeds)")
    async def cancel(self, interaction: discord.Interaction, event_id: int) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            r = await session.execute(
                select(Event).where(Event.id == event_id, Event.team_id == team.id)
            )
            event = r.scalar_one_or_none()
            if not event:
                await interaction.followup.send(
                    embed=error(t("calendar.event_not_found", interaction)),
                    ephemeral=True,
                )
                return

            await session.delete(event)
            await session.commit()
            await interaction.followup.send(
                embed=success(t("calendar.cancel_success_title", interaction), f"**{event.title}** a été supprimé du calendrier."),
                ephemeral=True,
            )

    # /cal result ───────────────────────────────────────────────────────────────

    @cal.command(name="result", description="Enregistrer le résultat d'un événement")
    @app_commands.describe(
        event_id="ID de l'événement",
        result="Résultat",
        rounds_won="Rounds gagnés",
        rounds_lost="Rounds perdus",
        notes="Notes post-match",
    )
    @app_commands.choices(result=RESULT_CHOICES)
    async def result(
        self,
        interaction: discord.Interaction,
        event_id: int,
        result: app_commands.Choice[str],
        rounds_won: Optional[int] = None,
        rounds_lost: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            r = await session.execute(
                select(Event).where(Event.id == event_id, Event.team_id == team.id)
            )
            event = r.scalar_one_or_none()
            if not event:
                await interaction.followup.send(
                    embed=error("Événement introuvable", f"ID `{event_id}` non trouvé.")
                )
                return

            event.result = MatchResult(result.value)
            if rounds_won is not None:
                event.rounds_won = rounds_won
            if rounds_lost is not None:
                event.rounds_lost = rounds_lost
            if notes:
                event.notes = notes

            await session.commit()
            await session.refresh(event)
            await interaction.followup.send(embed=event_embed(event))

    # /cal vod ──────────────────────────────────────────────────────────────────

    @cal.command(name="vod", description="Associer une VOD à un événement")
    @app_commands.describe(event_id="ID de l'événement", url="URL de la VOD")
    async def vod(self, interaction: discord.Interaction, event_id: int, url: str) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            r = await session.execute(
                select(Event).where(Event.id == event_id, Event.team_id == team.id)
            )
            event = r.scalar_one_or_none()
            if not event:
                await interaction.followup.send(embed=error("Événement introuvable"), ephemeral=True)
                return

            event.vod_url = url
            await session.commit()
            await interaction.followup.send(
                embed=success(t("calendar.vod_success_title", interaction), f"[Voir la VOD]({url}) → **{event.title}**"),
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CalendarCog(bot))
