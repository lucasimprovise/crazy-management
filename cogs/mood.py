"""
Mood cog — suivi hebdomadaire du mood de l'équipe.

Chaque joueur peut noter son moral de la semaine (1–5) et laisser
une note/justification. Le staff voit une vue d'ensemble anonymisée
ou détaillée selon les droits.

Un seul mood par joueur par semaine (upsert).
L'historique est conservé pour voir l'évolution.

Commands:
  /mood set <rating> [note]          — le joueur renseigne son mood
  /mood overview [team_name]         — staff : vue d'ensemble de la semaine
  /mood history [member] [team_name] — historique d'un joueur (staff) ou soi-même
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

from database import (
    get_session, Team, Player, TeamMember, TeamRole,
    MoodRating, TeamMood,
)
from utils.poster import post_mood_overview
from utils.cog_helpers import get_team_for_command
from utils.team_resolver import get_member_role
from utils.i18n import t
from config import config
from utils.i18n import tdict

logger = logging.getLogger(__name__)

RATING_CHOICES = [
    app_commands.Choice(name="😞 1 — Très mauvais / Very bad",    value="1"),
    app_commands.Choice(name="😕 2 — Mauvais / Bad",               value="2"),
    app_commands.Choice(name="😐 3 — Neutre / Neutral",            value="3"),
    app_commands.Choice(name="🙂 4 — Bon / Good",                  value="4"),
    app_commands.Choice(name="😄 5 — Excellent",                   value="5"),
]

MOOD_COLORS = {
    "1": 0xBD3944,
    "2": 0xF0A500,
    "3": 0x7289DA,
    "4": 0x43B581,
    "5": 0x00C17C,
}

MOOD_EMOJIS = {"1": "😞", "2": "😕", "3": "😐", "4": "🙂", "5": "😄"}


def _week_start(dt: datetime) -> datetime:
    """Return the Monday 00:00 of the week containing dt."""
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _avg_color(avg: float) -> int:
    if avg >= 4.5:
        return 0x00C17C
    if avg >= 3.5:
        return 0x43B581
    if avg >= 2.5:
        return 0x7289DA
    if avg >= 1.5:
        return 0xF0A500
    return 0xBD3944


class MoodCog(commands.Cog, name="Mood"):
    """Suivi du mood hebdomadaire de l'équipe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def team_autocomplete(self, interaction: discord.Interaction, current: str):
        async for session in get_session():
            r = await session.execute(select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True))
            return [
                app_commands.Choice(name=obj.name + (f" [{obj.tag}]" if obj.tag else ""), value=obj.name)
                for obj in r.scalars().all() if current.lower() in obj.name.lower()
            ][:25]

    mood = app_commands.Group(name="mood", description="Team mood / Mood de l'équipe")

    # /mood set ─────────────────────────────────────────────────────────────────

    @mood.command(name="set", description="Set your weekly mood · Renseigner son mood de la semaine")
    @app_commands.describe(
        rating="Your mood · Ton mood (1–5)",
        note="Justification / note (optional · optionnel)",
        team_name="Team · Équipe (optional)",
    )
    @app_commands.choices(rating=RATING_CHOICES)
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def set_mood(
        self,
        interaction: discord.Interaction,
        rating: app_commands.Choice[str],
        note: Optional[str] = None,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        week = _week_start(datetime.utcnow())

        async for session in get_session():
            team = await get_team_for_command(session, interaction, team_name)
            if not team:
                return

            # Get player
            player_r = await session.execute(
                select(Player).where(
                    Player.discord_id == interaction.user.id,
                    Player.team_id == team.id,
                    Player.is_active == True,
                )
            )
            player = player_r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌",
                        description=t("mood.not_in_roster", interaction),
                        color=0xBD3944,
                    ),
                    ephemeral=True,
                )
                return

            # Upsert mood
            existing_r = await session.execute(
                select(TeamMood).where(
                    TeamMood.player_id == player.id,
                    TeamMood.week_start == week,
                )
            )
            existing = existing_r.scalar_one_or_none()
            was_update = existing is not None

            if existing:
                existing.rating = MoodRating(rating.value)
                existing.note   = note
            else:
                session.add(TeamMood(
                    player_id  = player.id,
                    team_id    = team.id,
                    rating     = MoodRating(rating.value),
                    note       = note,
                    week_start = week,
                ))
            await session.commit()

            # Visual feedback in #mood-overview (staff only channel)
            if interaction.guild:
                await post_mood_overview(interaction.guild, session, team, week)

            ratings = tdict("mood.ratings", interaction)
            rating_label = ratings.get(rating.value, rating.value)

            embed = discord.Embed(
                title=f"{'✏️' if was_update else '✅'}  {t('mood.set_title', interaction)}",
                description=t("mood.set_description", interaction,
                               rating=rating_label,
                               week=week.strftime("%d/%m/%Y")),
                color=MOOD_COLORS.get(rating.value, 0xFF4655),
            )
            if note:
                embed.add_field(name="📝", value=f"_{note}_", inline=False)
            if was_update:
                embed.set_footer(text=t("mood.already_set", interaction))
            else:
                embed.set_footer(text=t("mood.footer", interaction, bot_name=config.bot_name))

            await interaction.followup.send(embed=embed, ephemeral=True)

    # /mood overview ─────────────────────────────────────────────────────────────

    @mood.command(name="overview", description="Team mood overview · Vue d'ensemble du mood")
    @app_commands.describe(team_name="Team · Équipe (optional)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def overview(self, interaction: discord.Interaction, team_name: Optional[str] = None) -> None:
        await interaction.response.defer()

        week = _week_start(datetime.utcnow())

        async for session in get_session():
            team = await get_team_for_command(session, interaction, team_name)
            if not team:
                return

            # Check if caller has at least Coach role to see notes
            caller_role = await get_member_role(session, team.id, interaction.user.id)
            is_staff = (
                caller_role in (TeamRole.OWNER, TeamRole.MANAGER, TeamRole.COACH)
                or (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator)
            )

            # Fetch all moods for this week
            moods_r = await session.execute(
                select(TeamMood)
                .where(TeamMood.team_id == team.id, TeamMood.week_start == week)
                .options(selectinload(TeamMood.player))
                .order_by(TeamMood.rating.desc())
            )
            moods = moods_r.scalars().all()

            # Total active players
            players_r = await session.execute(
                select(Player).where(Player.team_id == team.id, Player.is_active == True)
            )
            all_players = players_r.scalars().all()

            ratings_labels = tdict("mood.ratings", interaction)

            if not moods:
                embed = discord.Embed(
                    title=t("mood.overview_title", interaction, team=team.name),
                    description=(
                        f"{t('mood.overview_week', interaction, week=week.strftime('%d/%m/%Y'))}\n\n"
                        f"{t('mood.no_moods', interaction)}"
                    ),
                    color=0x7289DA,
                )
                embed.set_footer(text=t("mood.footer", interaction, bot_name=config.bot_name))
                await interaction.followup.send(embed=embed)
                return

            # Compute average
            avg = sum(int(m.rating.value) for m in moods) / len(moods)
            avg_emoji = MOOD_EMOJIS.get(str(round(avg)), "😐")

            # Participation rate
            participation = f"{len(moods)}/{len(all_players)}"

            embed = discord.Embed(
                title=t("mood.overview_title", interaction, team=team.name),
                description=(
                    f"{t('mood.overview_week', interaction, week=week.strftime('%d/%m/%Y'))}\n"
                    f"**{t('mood.avg_label', interaction)}** : {avg_emoji} `{avg:.1f}/5`  ·  "
                    f"Participation : `{participation}`\n\u200b"
                ),
                color=_avg_color(avg),
            )

            # Distribution bar
            dist: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
            for m in moods:
                dist[m.rating.value] += 1

            dist_lines = []
            for val in ["5", "4", "3", "2", "1"]:
                count = dist[val]
                bar = "█" * count + "░" * (len(moods) - count)
                label = ratings_labels.get(val, val)
                dist_lines.append(f"{label:<22} {bar} `{count}`")
            embed.add_field(name="📊 Distribution", value="\n".join(dist_lines), inline=False)

            # Individual entries
            for mood_entry in moods:
                player = mood_entry.player
                rating_label = ratings_labels.get(mood_entry.rating.value, mood_entry.rating.value)
                emoji = MOOD_EMOJIS.get(mood_entry.rating.value, "😐")

                # Staff see the notes; players see anonymous rating only
                if is_staff:
                    note_str = f"\n_{mood_entry.note}_" if mood_entry.note else ""
                    embed.add_field(
                        name=f"{emoji} {player.ign}",
                        value=f"`{rating_label}`{note_str}",
                        inline=True,
                    )
                else:
                    embed.add_field(
                        name=f"{emoji} {player.ign}",
                        value=f"`{rating_label}`",
                        inline=True,
                    )

            # Players who haven't set their mood
            mood_player_ids = {m.player_id for m in moods}
            missing = [p for p in all_players if p.id not in mood_player_ids]
            if missing:
                embed.add_field(
                    name="⏳ Pas encore renseigné / Not set yet",
                    value=", ".join(f"**{p.ign}**" for p in missing),
                    inline=False,
                )

            embed.set_footer(text=t("mood.footer", interaction, bot_name=config.bot_name))
            await interaction.followup.send(embed=embed)

    # /mood history ─────────────────────────────────────────────────────────────

    @mood.command(name="history", description="Mood history · Historique des moods")
    @app_commands.describe(
        member="Player (staff only · staff seulement pour voir les autres)",
        team_name="Team · Équipe (optional)",
    )
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def history(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=member is not None)

        async for session in get_session():
            team = await get_team_for_command(session, interaction, team_name)
            if not team:
                return

            target = member or interaction.user

            # Permission check: only staff can see others' history
            if target.id != interaction.user.id:
                caller_role = await get_member_role(session, team.id, interaction.user.id)
                is_staff = (
                    caller_role in (TeamRole.OWNER, TeamRole.MANAGER, TeamRole.COACH)
                    or (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator)
                )
                if not is_staff:
                    await interaction.followup.send(
                        embed=discord.Embed(
                            title="❌",
                            description="Seul le staff peut voir le mood des autres joueurs.",
                            color=0xBD3944,
                        ),
                        ephemeral=True,
                    )
                    return

            # Get player
            player_r = await session.execute(
                select(Player).where(Player.discord_id == target.id, Player.team_id == team.id)
            )
            player = player_r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=discord.Embed(title="❌", description=t("mood.not_in_roster", interaction), color=0xBD3944),
                    ephemeral=True,
                )
                return

            # Fetch last 8 weeks
            moods_r = await session.execute(
                select(TeamMood)
                .where(TeamMood.player_id == player.id)
                .order_by(TeamMood.week_start.desc())
                .limit(8)
            )
            moods = moods_r.scalars().all()

            ratings_labels = tdict("mood.ratings", interaction)

            if not moods:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title=t("mood.history_title", interaction, ign=player.ign),
                        description=t("mood.no_history", interaction),
                        color=0x7289DA,
                    )
                )
                return

            avg = sum(int(m.rating.value) for m in moods) / len(moods)
            embed = discord.Embed(
                title=t("mood.history_title", interaction, ign=player.ign),
                description=f"Moyenne sur {len(moods)} semaine(s) : {MOOD_EMOJIS.get(str(round(avg)), '😐')} `{avg:.1f}/5`\n\u200b",
                color=_avg_color(avg),
            )
            embed.set_thumbnail(url=target.display_avatar.url)

            for mood_entry in moods:
                emoji = MOOD_EMOJIS.get(mood_entry.rating.value, "😐")
                label = ratings_labels.get(mood_entry.rating.value, mood_entry.rating.value)
                week_str = mood_entry.week_start.strftime("%d/%m/%Y")
                note_str = f"\n_{mood_entry.note}_" if mood_entry.note else ""
                embed.add_field(
                    name=f"{emoji} Semaine du {week_str}",
                    value=f"`{label}`{note_str}",
                    inline=True,
                )

            embed.set_footer(text=t("mood.footer", interaction, bot_name=config.bot_name))
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MoodCog(bot))
