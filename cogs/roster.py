"""
Roster cog — gestion des joueurs d'une équipe.
Utilise TeamResolver pour la résolution multi-équipes.
"""

from __future__ import annotations
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from database import get_session, Team, Player, TeamRole
from utils import success, error, warning, roster_embed
from utils.i18n import t
from utils.channels import get_team_channels, auto_assign_role, auto_remove_role
from utils.poster import post_roster_update
from utils.team_resolver import resolve_team

logger = logging.getLogger(__name__)

ROLE_CHOICES = [
    app_commands.Choice(name="🎯 IGL",         value="IGL"),
    app_commands.Choice(name="⚔️  Duelist",    value="Duelist"),
    app_commands.Choice(name="🔦 Initiator",   value="Initiator"),
    app_commands.Choice(name="🛡️  Sentinel",   value="Sentinel"),
    app_commands.Choice(name="💨 Controller",  value="Controller"),
    app_commands.Choice(name="🎙️ Coach",       value="Coach"),
    app_commands.Choice(name="📋 Analyst",     value="Analyst"),
]


class RosterCog(commands.Cog, name="Roster"):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def team_autocomplete(self, interaction: discord.Interaction, current: str):
        async for session in get_session():
            r = await session.execute(
                select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True)
            )
            return [
                app_commands.Choice(name=t.name, value=t.name)
                for t in r.scalars().all()
                if current.lower() in t.name.lower()
            ][:25]

    roster = app_commands.Group(name="roster", description="Gestion du roster")

    @roster.command(name="add", description="Ajouter un joueur au roster")
    @app_commands.describe(
        member="Membre Discord",
        ign="Riot ID (ex: TenZ)",
        tag="Riot Tag (ex: NA1)",
        role="Rôle en jeu",
        team_name="Équipe (optionnel si une seule)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        ign: str,
        tag: str,
        role: Optional[app_commands.Choice[str]] = None,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team:
                return

            existing_r = await session.execute(
                select(Player).where(Player.discord_id == member.id, Player.team_id == team.id)
            )
            if existing_r.scalar_one_or_none():
                await interaction.followup.send(
                    embed=warning(t("roster.already_in_roster_title", interaction), f"{member.mention} est déjà dans le roster de **{team.name}**."),
                    ephemeral=True,
                )
                return

            player = Player(
                discord_id=member.id,
                team_id=team.id,
                ign=ign,
                tag=tag,
                role=role.value if role else None,
            )
            session.add(player)
            await session.commit()

            # Auto-assign Discord role
            if isinstance(interaction.user, discord.Member):
                tc = await get_team_channels(session, team.id)
                if tc:
                    await auto_assign_role(interaction.guild, member, tc, is_staff=False)

            # Visual feedback in #roster
            if interaction.guild:
                await post_roster_update(
                    interaction.guild, session, team,
                    "added", ign, role.value if role else None, interaction.user,
                )

            await interaction.followup.send(
                embed=success(
                    t("roster.add_success_title", interaction),
                    f"{member.mention} → **{ign}#{tag}**"
                    + (f" · `{role.value}`" if role else "")
                    + f" · Équipe : **{team.name}**",
                ),
                ephemeral=False,
            )

    @roster.command(name="remove", description="Retirer un joueur du roster")
    @app_commands.describe(member="Membre Discord", team_name="Équipe (optionnel)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team:
                return

            r = await session.execute(
                select(Player).where(Player.discord_id == member.id, Player.team_id == team.id)
            )
            player = r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=warning(t("roster.player_not_found", interaction), f"{member.mention} n'est pas dans le roster."),
                    ephemeral=True,
                )
                return

            saved_ign = player.ign
            await session.delete(player)
            await session.commit()

            # Remove Discord roles
            if isinstance(member, discord.Member):
                tc = await get_team_channels(session, team.id)
                if tc:
                    await auto_remove_role(interaction.guild, member, tc)

            # Visual feedback in #roster
            if interaction.guild:
                await post_roster_update(
                    interaction.guild, session, team,
                    "removed", saved_ign, None, interaction.user,
                )

            await interaction.followup.send(
                embed=success(t("roster.remove_success_title", interaction), f"**{saved_ign}** retiré de **{team.name}**."),
                ephemeral=True,
            )

    @roster.command(name="list", description="Afficher le roster d'une équipe")
    @app_commands.describe(team_name="Équipe (optionnel)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def list_roster(self, interaction: discord.Interaction, team_name: Optional[str] = None) -> None:
        await interaction.response.defer()
        async for session in get_session():
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id, interaction, team_name
            )
            if not team:
                return

            r = await session.execute(
                select(Player).where(Player.team_id == team.id, Player.is_active == True)
            )
            players = r.scalars().all()
            await interaction.followup.send(embed=roster_embed(team.name, players))

    @roster.command(name="info", description="Profil d'un joueur")
    @app_commands.describe(member="Membre Discord", team_name="Équipe (optionnel)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def info(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        async for session in get_session():
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id, interaction, team_name
            )
            if not team:
                return

            r = await session.execute(
                select(Player).where(Player.discord_id == member.id, Player.team_id == team.id)
            )
            player = r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=warning("Introuvable", f"{member.mention} n'est pas dans le roster de **{team.name}**.")
                )
                return

            embed = discord.Embed(title=f"👤  {player.ign}#{player.tag or '???'}", color=0xFF4655)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Discord",  value=member.mention,                     inline=True)
            embed.add_field(name="Rôle",     value=f"`{player.role or 'N/A'}`",        inline=True)
            embed.add_field(name="Équipe",   value=f"**{team.name}**",                  inline=True)
            embed.add_field(name="Inscrit",  value=f"<t:{int(player.joined_at.timestamp())}:D>", inline=True)
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RosterCog(bot))
