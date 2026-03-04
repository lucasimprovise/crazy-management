"""
Team cog — création et gestion des équipes multi-tenant.
Crée automatiquement les channels et rôles Discord à la création d'équipe.
"""
from __future__ import annotations
import logging
from typing import Optional

import discord
from config import config
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from database import get_session, Team, TeamMember, TeamRole, GuildContext, Player
from utils.team_resolver import resolve_team, get_member_role, ROLE_HIERARCHY
from utils.channels import setup_team_channels, delete_team_channels
from utils.i18n import t
from utils import success, error, warning

logger = logging.getLogger(__name__)

REGION_CHOICES = [
    app_commands.Choice(name="🇪🇺 Europe",        value="eu"),
    app_commands.Choice(name="🇺🇸 North America", value="na"),
    app_commands.Choice(name="🌏 Asia Pacific",   value="ap"),
    app_commands.Choice(name="🇰🇷 Korea",          value="kr"),
]

ROLE_CHOICES = [
    app_commands.Choice(name="📋 Manager", value="manager"),
    app_commands.Choice(name="🎙️ Coach",   value="coach"),
    app_commands.Choice(name="🎮 Player",  value="player"),
]


class TeamCog(commands.Cog, name="Teams"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def team_autocomplete(self, interaction: discord.Interaction, current: str):
        async for session in get_session():
            r = await session.execute(
                select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True)
            )
            return [
                app_commands.Choice(name=obj.name + (f" [{obj.tag}]" if obj.tag else ""), value=obj.name)
                for obj in r.scalars().all()
                if current.lower() in obj.name.lower()
            ][:25]

    team = app_commands.Group(name="team", description="Team management / Gestion des équipes")

    # /team create ──────────────────────────────────────────────────────────────

    @team.command(name="create", description="Create a team + auto-setup channels · Créer une équipe")
    @app_commands.describe(
        name="Team name · Nom de l'équipe",
        tag="Short tag · Tag court (ex: NVI, G2, FNC)",
        region="Region · Région",
        setup_channels="Auto-create Discord channels · Créer les channels automatiquement (défaut: oui)",
    )
    @app_commands.choices(region=REGION_CHOICES)
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        tag: Optional[str] = None,
        region: Optional[app_commands.Choice[str]] = None,
        setup_channels: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            # Check duplicate name
            existing = await session.execute(
                select(Team).where(Team.guild_id == interaction.guild_id, Team.name.ilike(name))
            )
            if existing.scalar_one_or_none():
                await interaction.followup.send(
                    embed=error(t("team.name_taken_title", interaction), t("team.name_taken", interaction, name=name)),
                    ephemeral=True,
                )
                return

            team_obj = Team(
                guild_id   = interaction.guild_id,
                name       = name,
                tag        = tag,
                region     = region.value if region else None,
                created_by = interaction.user.id,
            )
            session.add(team_obj)
            await session.flush()

            # Owner role
            session.add(TeamMember(team_id=team_obj.id, discord_id=interaction.user.id, role=TeamRole.OWNER))

            # Active context
            ctx_r = await session.execute(
                select(GuildContext).where(
                    GuildContext.guild_id == interaction.guild_id,
                    GuildContext.discord_id == interaction.user.id,
                )
            )
            ctx = ctx_r.scalar_one_or_none()
            if ctx:
                ctx.active_team_id = team_obj.id
            else:
                session.add(GuildContext(
                    guild_id=interaction.guild_id,
                    discord_id=interaction.user.id,
                    active_team_id=team_obj.id,
                ))
            await session.commit()
            await session.refresh(team_obj)

            # ── Auto-create channels ──────────────────────────────────────────
            channels_ok = False
            channels_error = False

            if setup_channels:
                # Need Manage Channels permission
                if not interaction.guild.me.guild_permissions.manage_channels:
                    channels_error = True
                else:
                    tc = await setup_team_channels(interaction.guild, team_obj, session)
                    channels_ok = tc is not None
                    if not channels_ok:
                        channels_error = True

            # ── Build response embed ──────────────────────────────────────────
            embed = discord.Embed(title=t("team.created_title", interaction), color=0xFF4655)
            embed.add_field(name=t("team.field_name", interaction),   value=f"**{name}**" + (f" `[{tag}]`" if tag else ""), inline=True)
            embed.add_field(name=t("team.field_region", interaction), value=f"`{region.value if region else 'N/A'}`", inline=True)
            embed.add_field(name=t("team.field_role", interaction),   value="👑 Owner", inline=True)

            if setup_channels:
                if channels_ok:
                    embed.add_field(
                        name="✅ Channels créés",
                        value=(
                            "Une catégorie publique et une catégorie staff ont été créées.\n"
                            "Les rôles `{tag} Staff` et `{tag} Player` ont été configurés.".format(
                                tag=tag or name[:3].upper()
                            )
                        ),
                        inline=False,
                    )
                elif channels_error:
                    embed.add_field(
                        name="⚠️ Channels non créés",
                        value=(
                            "Le bot n'a pas la permission **Gérer les salons**.\n"
                            "Donne-lui cette permission ou utilise `/team setup-channels` plus tard."
                        ),
                        inline=False,
                    )

            embed.description = t("team.created_next_steps", interaction)
            embed.set_footer(text=config.bot_name)
            await interaction.followup.send(embed=embed, ephemeral=False)

    # /team setup-channels ──────────────────────────────────────────────────────

    @team.command(name="setup-channels", description="(Re)create channels for a team · (Re)créer les channels d'une équipe")
    @app_commands.describe(team_name="Team · Équipe (optional)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def setup_channels_cmd(
        self,
        interaction: discord.Interaction,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team_obj = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.OWNER,
            )
            if not team_obj:
                return

            if not interaction.guild.me.guild_permissions.manage_channels:
                await interaction.followup.send(
                    embed=error(
                        "Permission manquante",
                        "Le bot a besoin de la permission **Gérer les salons** (`manage_channels`) pour créer les channels.\n"
                        "Donne-lui cette permission dans les paramètres du serveur.",
                    ),
                    ephemeral=True,
                )
                return

            await interaction.followup.send(
                embed=discord.Embed(title="⏳ Création des channels...", color=0xFF4655),
                ephemeral=True,
            )

            tc = await setup_team_channels(interaction.guild, team_obj, session)

            if tc:
                tag = team_obj.tag or team_obj.name[:3].upper()
                await interaction.edit_original_response(
                    embed=success(
                        "Channels créés !",
                        f"Structure créée pour **{team_obj.name}** :\n\n"
                        f"**Catégorie publique** `〔🎮〕 {team_obj.name}`\n"
                        f"• 📢・annonces\n• 📅・calendrier\n• 👥・roster\n• 🥊・praccs\n• 💬・général\n\n"
                        f"**Catégorie staff** `〔🔒〕 {team_obj.name} Staff`\n"
                        f"• 🎙️・staff-général\n• 💬・mood-overview\n• 📋・logs-bot\n\n"
                        f"**Rôles** : `@{tag} Staff` · `@{tag} Player`",
                    )
                )
            else:
                await interaction.edit_original_response(
                    embed=error("Échec", "Impossible de créer les channels. Vérifie les permissions du bot.")
                )

    # /team list ────────────────────────────────────────────────────────────────

    @team.command(name="list", description="List all teams · Voir toutes les équipes")
    async def list_teams(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        async for session in get_session():
            result = await session.execute(
                select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True)
            )
            teams = result.scalars().all()
            if not teams:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title=t("team.no_teams_title", interaction),
                        description=t("team.no_teams_description", interaction),
                        color=0xFF4655,
                    )
                )
                return

            embed = discord.Embed(
                title=t("team.list_title", interaction, guild=interaction.guild.name),
                description=t("team.list_description", interaction, count=len(teams)) + "\n\u200b",
                color=0xFF4655,
            )
            for team_obj in teams:
                members_r = await session.execute(select(TeamMember).where(TeamMember.team_id == team_obj.id))
                players_r = await session.execute(select(Player).where(Player.team_id == team_obj.id, Player.is_active == True))
                mc = len(members_r.scalars().all())
                pc = len(players_r.scalars().all())
                embed.add_field(
                    name=f"**{team_obj.name}**" + (f" `[{team_obj.tag}]`" if team_obj.tag else ""),
                    value=(
                        f"🌍 `{team_obj.region or 'N/A'}` · "
                        f"👥 {pc} {t('team.players_count', interaction)} · "
                        f"🎙️ {mc} {t('team.members_count', interaction)}\n"
                        f"*{t('team.created_at', interaction)} <t:{int(team_obj.created_at.timestamp())}:d>*"
                    ),
                    inline=False,
                )
            embed.set_footer(text=config.bot_name)
            await interaction.followup.send(embed=embed)

    # /team switch ──────────────────────────────────────────────────────────────

    @team.command(name="switch", description="Change active team · Changer d'équipe active")
    @app_commands.describe(team_name="Team name · Nom de l'équipe")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def switch(self, interaction: discord.Interaction, team_name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team_r = await session.execute(
                select(Team).where(Team.guild_id == interaction.guild_id, Team.name.ilike(team_name), Team.is_active == True)
            )
            team_obj = team_r.scalar_one_or_none()
            if not team_obj:
                await interaction.followup.send(embed=error("Error", t("team.not_found", interaction, name=team_name)), ephemeral=True)
                return
            member_r = await session.execute(
                select(TeamMember).where(TeamMember.team_id == team_obj.id, TeamMember.discord_id == interaction.user.id)
            )
            if not member_r.scalar_one_or_none():
                await interaction.followup.send(embed=error("Error", t("team.switch_not_member", interaction, name=team_obj.name)), ephemeral=True)
                return
            ctx_r = await session.execute(
                select(GuildContext).where(GuildContext.guild_id == interaction.guild_id, GuildContext.discord_id == interaction.user.id)
            )
            ctx = ctx_r.scalar_one_or_none()
            if ctx:
                ctx.active_team_id = team_obj.id
            else:
                session.add(GuildContext(guild_id=interaction.guild_id, discord_id=interaction.user.id, active_team_id=team_obj.id))
            await session.commit()
            await interaction.followup.send(
                embed=success(t("team.switch_success_title", interaction), t("team.switch_success", interaction, name=team_obj.name)),
                ephemeral=True,
            )

    # /team invite ──────────────────────────────────────────────────────────────

    @team.command(name="invite", description="Invite a member · Inviter un membre")
    @app_commands.describe(
        member="Discord member · Membre Discord",
        role="Team role · Rôle dans l'équipe",
        team_name="Team · Équipe (optional)",
    )
    @app_commands.choices(role=ROLE_CHOICES)
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def invite(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        role: app_commands.Choice[str],
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team_obj = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team_obj:
                return

            new_role = TeamRole(role.value)
            if new_role == TeamRole.MANAGER:
                caller_role = await get_member_role(session, team_obj.id, interaction.user.id)
                if caller_role != TeamRole.OWNER and not interaction.user.guild_permissions.administrator:
                    await interaction.followup.send(
                        embed=error("Permission", t("team.only_owner_can_assign_manager", interaction)),
                        ephemeral=True,
                    )
                    return

            existing_r = await session.execute(
                select(TeamMember).where(TeamMember.team_id == team_obj.id, TeamMember.discord_id == member.id)
            )
            existing = existing_r.scalar_one_or_none()
            if existing:
                existing.role = new_role
            else:
                session.add(TeamMember(team_id=team_obj.id, discord_id=member.id, role=new_role))
            await session.commit()

            # Auto-assign Discord role
            from utils.channels import get_team_channels, auto_assign_role
            tc = await get_team_channels(session, team_obj.id)
            if tc:
                is_staff = new_role in (TeamRole.MANAGER, TeamRole.COACH)
                await auto_assign_role(interaction.guild, member, tc, is_staff=is_staff)

            role_labels = {"manager": "📋 Manager", "coach": "🎙️ Coach", "player": "🎮 Player"}
            await interaction.followup.send(
                embed=success(
                    t("team.invite_success_title", interaction),
                    t("team.invite_success", interaction, member=member.mention, team=team_obj.name, role=role_labels.get(role.value, role.value)),
                ),
                ephemeral=False,
            )

    # /team kick ────────────────────────────────────────────────────────────────

    @team.command(name="kick", description="Remove a member · Retirer un membre")
    @app_commands.describe(member="Member to remove · Membre à retirer", team_name="Team · Équipe (optional)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        team_name: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team_obj = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team_obj:
                return

            target_role = await get_member_role(session, team_obj.id, member.id)
            caller_role = await get_member_role(session, team_obj.id, interaction.user.id)
            if target_role == TeamRole.OWNER:
                await interaction.followup.send(embed=error("Error", t("team.kick_owner_forbidden", interaction)), ephemeral=True)
                return
            if caller_role != TeamRole.OWNER and ROLE_HIERARCHY.get(target_role, 0) >= ROLE_HIERARCHY.get(caller_role, 0):
                await interaction.followup.send(embed=error("Permission", t("team.kick_superior_forbidden", interaction)), ephemeral=True)
                return

            member_r = await session.execute(
                select(TeamMember).where(TeamMember.team_id == team_obj.id, TeamMember.discord_id == member.id)
            )
            tm = member_r.scalar_one_or_none()
            if not tm:
                await interaction.followup.send(embed=warning("Not found", t("team.kick_not_member", interaction, member=member.mention, team=team_obj.name)), ephemeral=True)
                return

            await session.delete(tm)
            await session.commit()

            # Remove Discord roles
            from utils.channels import get_team_channels, auto_remove_role
            tc = await get_team_channels(session, team_obj.id)
            if tc:
                await auto_remove_role(interaction.guild, member, tc)

            await interaction.followup.send(
                embed=success("✅", t("team.kick_success", interaction, member=member.mention, team=team_obj.name)),
                ephemeral=True,
            )

    # /team delete ──────────────────────────────────────────────────────────────

    @team.command(name="delete", description="Delete a team · Supprimer une équipe (Owner only)")
    @app_commands.describe(team_name="Team name · Nom de l'équipe")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def delete(self, interaction: discord.Interaction, team_name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team_obj = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.OWNER,
            )
            if not team_obj:
                return

            class ConfirmView(discord.ui.View):
                def __init__(self_v):
                    super().__init__(timeout=15)
                    self_v.confirmed = False

                @discord.ui.button(label=t("team.delete_confirm_btn", interaction), style=discord.ButtonStyle.danger)
                async def confirm(self_v, btn_i: discord.Interaction, btn: discord.ui.Button):
                    self_v.confirmed = True; self_v.stop(); await btn_i.response.defer()

                @discord.ui.button(label=t("team.delete_cancel_btn", interaction), style=discord.ButtonStyle.secondary)
                async def cancel(self_v, btn_i: discord.Interaction, btn: discord.ui.Button):
                    self_v.stop(); await btn_i.response.defer()

            view = ConfirmView()
            embed = discord.Embed(
                title=t("team.delete_confirm_title", interaction),
                description=t("team.delete_confirm_description", interaction, name=team_obj.name),
                color=0xF0A500,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if not view.confirmed:
                await interaction.edit_original_response(embed=warning("", t("general.cancelled", interaction)), view=None)
                return

            saved_name = team_obj.name

            # Delete channels + roles first
            await delete_team_channels(interaction.guild, team_obj, session)

            await session.delete(team_obj)
            await session.commit()
            await interaction.edit_original_response(
                embed=success(t("team.delete_success_title", interaction), t("team.delete_success", interaction, name=saved_name)),
                view=None,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamCog(bot))
