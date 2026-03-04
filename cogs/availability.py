"""
Availability cog — players set their weekly availability.

Commands:
  /dispo add <date> <slot> [note]   — set availability
  /dispo remove <date> <slot>       — remove availability
  /dispo week [date]                — show team's weekly overview
  /dispo mine                       — show own availability
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from database import get_session, Team, TeamRole, Player, Availability, AvailabilitySlot
from utils.cog_helpers import get_team_for_command
from utils import success, error, warning, availability_week_embed
from utils.i18n import t, tlist, tdict

logger = logging.getLogger(__name__)

SLOT_CHOICES = [
    app_commands.Choice(name="🌅 Matin (08h–12h)", value="morning"),
    app_commands.Choice(name="☀️ Après-midi (12h–18h)", value="afternoon"),
    app_commands.Choice(name="🌙 Soir (18h–23h)", value="evening"),
    app_commands.Choice(name="📅 Toute la journée", value="all_day"),
]

DAY_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MONTH_FR = [
    "", "Jan", "Fév", "Mar", "Avr", "Mai", "Jun",
    "Jul", "Aoû", "Sep", "Oct", "Nov", "Déc",
]


def fmt_day(dt: datetime) -> str:
    return f"{DAY_FR[dt.weekday()]} {dt.day:02d}/{dt.month:02d}"


class AvailabilityCog(commands.Cog, name="Disponibilités"):
    """Suivi des disponibilités des joueurs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_team(self, session, guild_id: int) -> Team | None:
        r = await session.execute(select(Team).where(Team.guild_id == guild_id))
        return r.scalar_one_or_none()

    async def _get_player(self, session, discord_id: int, team_id: int) -> Player | None:
        r = await session.execute(
            select(Player).where(Player.discord_id == discord_id, Player.team_id == team_id)
        )
        return r.scalar_one_or_none()

    # ── Group ─────────────────────────────────────────────────────────────────

    dispo = app_commands.Group(name="dispo", description="Gestion des disponibilités")

    # /dispo add ────────────────────────────────────────────────────────────────

    @dispo.command(name="add", description="Ajouter une disponibilité")
    @app_commands.describe(
        date="Date au format JJ/MM/AAAA (ex: 15/03/2025)",
        slot="Créneau horaire",
        note="Note optionnelle",
    )
    @app_commands.choices(slot=SLOT_CHOICES)
    async def add(
        self,
        interaction: discord.Interaction,
        date: str,
        slot: app_commands.Choice[str],
        note: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            dt = datetime.strptime(date, "%d/%m/%Y")
        except ValueError:
            await interaction.followup.send(
                embed=error(t("general.invalid_date", interaction)),
                ephemeral=True,
            )
            return

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            player = await self._get_player(session, interaction.user.id, team.id)
            if not player:
                await interaction.followup.send(
                    embed=error(t("availability.not_found_title", interaction), t("availability.not_in_roster", interaction)),
                    ephemeral=True,
                )
                return

            # Upsert: delete existing then re-add
            existing = await session.execute(
                select(Availability).where(
                    and_(
                        Availability.player_id == player.id,
                        Availability.date == dt,
                        Availability.slot == AvailabilitySlot(slot.value),
                    )
                )
            )
            existing_row = existing.scalar_one_or_none()
            if existing_row:
                await interaction.followup.send(
                    embed=warning(t("availability.already_exists_title", interaction), f"Tu as déjà renseigné ce créneau pour le {date}."),
                    ephemeral=True,
                )
                return

            availability = Availability(
                player_id=player.id,
                date=dt,
                slot=AvailabilitySlot(slot.value),
                note=note,
            )
            session.add(availability)
            await session.commit()

            await interaction.followup.send(
                embed=success(
                    t("availability.add_success_title", interaction),
                    f"**{fmt_day(dt)}** · {slot.name}" + (f"\n📝 {note}" if note else ""),
                ),
                ephemeral=True,
            )

    # /dispo remove ─────────────────────────────────────────────────────────────

    @dispo.command(name="remove", description="Supprimer une disponibilité")
    @app_commands.describe(date="Date (JJ/MM/AAAA)", slot="Créneau à supprimer")
    @app_commands.choices(slot=SLOT_CHOICES)
    async def remove(
        self,
        interaction: discord.Interaction,
        date: str,
        slot: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            dt = datetime.strptime(date, "%d/%m/%Y")
        except ValueError:
            await interaction.followup.send(embed=error(t("general.invalid_date", interaction)), ephemeral=True)
            return

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            player = await self._get_player(session, interaction.user.id, team.id)
            if not player:
                await interaction.followup.send(embed=error("Non inscrit au roster"), ephemeral=True)
                return

            result = await session.execute(
                select(Availability).where(
                    and_(
                        Availability.player_id == player.id,
                        Availability.date == dt,
                        Availability.slot == AvailabilitySlot(slot.value),
                    )
                )
            )
            row = result.scalar_one_or_none()
            if not row:
                await interaction.followup.send(
                    embed=warning(t("availability.not_found_title", interaction), "Aucune disponibilité trouvée pour ce créneau."),
                    ephemeral=True,
                )
                return

            await session.delete(row)
            await session.commit()
            await interaction.followup.send(
                embed=success(t("availability.remove_success_title", interaction), f"{fmt_day(dt)} · {slot.name}"),
                ephemeral=True,
            )

    # /dispo week ───────────────────────────────────────────────────────────────

    @dispo.command(name="week", description="Voir les disponibilités de l'équipe cette semaine")
    @app_commands.describe(date="Optionnel — date de début de semaine (JJ/MM/AAAA)")
    async def week(self, interaction: discord.Interaction, date: Optional[str] = None) -> None:
        await interaction.response.defer()

        if date:
            try:
                start = datetime.strptime(date, "%d/%m/%Y")
            except ValueError:
                await interaction.followup.send(embed=error("Format de date invalide"))
                return
        else:
            today = datetime.utcnow()
            # Start of current week (Monday)
            start = today - timedelta(days=today.weekday())

        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)

        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"))
                return

            # Fetch all availabilities for the week with player info
            result = await session.execute(
                select(Availability)
                .join(Player)
                .where(
                    and_(
                        Player.team_id == team.id,
                        Availability.date >= start,
                        Availability.date < end,
                    )
                )
                .options(selectinload(Availability.player))
            )
            rows = result.scalars().all()

            # Build week_data dict
            week_data: dict[str, dict[str, list[str]]] = {}
            for i in range(7):
                day = start + timedelta(days=i)
                week_data[fmt_day(day)] = {"morning": [], "afternoon": [], "evening": [], "all_day": []}

            for avail in rows:
                day_key = fmt_day(avail.date)
                if day_key in week_data:
                    week_data[day_key][avail.slot.value].append(avail.player.ign)

            # Remove empty days
            week_data = {k: v for k, v in week_data.items() if any(v.values())}

            week_label = f"{fmt_day(start)} → {fmt_day(end - timedelta(days=1))}"
            await interaction.followup.send(
                embed=availability_week_embed(team.name, week_data)
                if week_data
                else discord.Embed(
                    title="📅 Aucune disponibilité",
                    description=f"Personne n'a renseigné ses dispos pour la semaine du **{week_label}**.",
                    color=0xFF4655,
                )
            )

    # /dispo mine ───────────────────────────────────────────────────────────────

    @dispo.command(name="mine", description="Voir mes propres disponibilités à venir")
    async def mine(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        now = datetime.utcnow()
        async for session in get_session():
            team = await self._get_team(session, interaction.guild_id)
            if not team:
                await interaction.followup.send(embed=error("Équipe non configurée"), ephemeral=True)
                return

            player = await self._get_player(session, interaction.user.id, team.id)
            if not player:
                await interaction.followup.send(embed=error("Non inscrit au roster"), ephemeral=True)
                return

            result = await session.execute(
                select(Availability)
                .where(and_(Availability.player_id == player.id, Availability.date >= now))
                .order_by(Availability.date)
                .limit(20)
            )
            rows = result.scalars().all()

            if not rows:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="📅 Aucune disponibilité",
                        description="Tu n'as renseigné aucune dispo à venir. Utilise `/dispo add`.",
                        color=0xFF4655,
                    ),
                    ephemeral=True,
                )
                return

            embed = discord.Embed(title=f"📅 Mes disponibilités — {player.ign}", color=0xFF4655)
            for avail in rows:
                embed.add_field(
                    name=fmt_day(avail.date),
                    value=f"`{avail.slot.value}`" + (f" · _{avail.note}_" if avail.note else ""),
                    inline=True,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AvailabilityCog(bot))
