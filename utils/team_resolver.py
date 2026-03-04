"""
TeamResolver — résolution de l'équipe active pour chaque utilisateur.
Supporte i18n FR/EN.
"""
from __future__ import annotations
import discord
from discord import ui
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Team, TeamMember, TeamRole, GuildContext
from utils.i18n import t


class TeamSelectView(ui.View):
    def __init__(self, teams: list[Team], timeout: float = 30.0) -> None:
        super().__init__(timeout=timeout)
        self.selected: Team | None = None
        for team in teams[:5]:
            btn = ui.Button(
                label=f"{team.name}" + (f" [{team.tag}]" if team.tag else ""),
                style=discord.ButtonStyle.primary,
                custom_id=str(team.id),
            )
            btn.callback = self._make_callback(team)
            self.add_item(btn)

    def _make_callback(self, team: Team):
        async def callback(interaction: discord.Interaction) -> None:
            self.selected = team
            self.stop()
            await interaction.response.defer()
        return callback


async def resolve_team(
    session: AsyncSession,
    guild_id: int,
    discord_id: int,
    interaction: discord.Interaction,
    team_name: str | None = None,
    require_role: TeamRole | None = None,
) -> Team | None:
    # 1. Explicit name
    if team_name:
        result = await session.execute(
            select(Team).where(Team.guild_id == guild_id, Team.name.ilike(team_name), Team.is_active == True)
        )
        team = result.scalar_one_or_none()
        if not team:
            await _send_error(interaction, "Error", t("general.team_not_found", interaction, name=team_name))
            return None
        if require_role:
            return await _check_role(session, interaction, team, discord_id, require_role)
        return team

    # 2. Memberships
    members_result = await session.execute(
        select(TeamMember).join(Team).where(Team.guild_id == guild_id, TeamMember.discord_id == discord_id, Team.is_active == True)
    )
    memberships = members_result.scalars().all()

    if not memberships:
        teams_result = await session.execute(select(Team).where(Team.guild_id == guild_id, Team.is_active == True))
        guild_teams = teams_result.scalars().all()
        if not guild_teams:
            await _send_error(interaction, "Error", t("general.team_not_configured", interaction))
        else:
            await _send_error(interaction, "Error", t("general.not_in_roster", interaction))
        return None

    # 3. Single team → auto-resolve
    if len(memberships) == 1:
        result = await session.execute(select(Team).where(Team.id == memberships[0].team_id))
        team = result.scalar_one_or_none()
        if require_role:
            return await _check_role(session, interaction, team, discord_id, require_role)
        return team

    # 4. Check saved context
    ctx_result = await session.execute(
        select(GuildContext).where(GuildContext.guild_id == guild_id, GuildContext.discord_id == discord_id)
    )
    ctx = ctx_result.scalar_one_or_none()
    if ctx and ctx.active_team_id:
        member_ids = {m.team_id for m in memberships}
        if ctx.active_team_id in member_ids:
            result = await session.execute(select(Team).where(Team.id == ctx.active_team_id, Team.is_active == True))
            team = result.scalar_one_or_none()
            if team:
                if require_role:
                    return await _check_role(session, interaction, team, discord_id, require_role)
                return team

    # 5. Interactive selector
    team_ids = [m.team_id for m in memberships]
    teams_result = await session.execute(select(Team).where(Team.id.in_(team_ids), Team.is_active == True))
    teams = teams_result.scalars().all()

    embed = discord.Embed(
        title=t("general.select_team_title", interaction),
        description=t("general.select_team_description", interaction),
        color=0xFF4655,
    )
    for team_obj in teams:
        embed.add_field(name=f"{team_obj.name}" + (f" [{team_obj.tag}]" if team_obj.tag else ""), value=f"🌍 `{team_obj.region or 'N/A'}`", inline=True)
    embed.set_footer(text=t("general.select_team_footer", interaction))

    view = TeamSelectView(teams)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    await view.wait()
    if view.selected is None:
        return None

    if ctx:
        ctx.active_team_id = view.selected.id
    else:
        session.add(GuildContext(guild_id=guild_id, discord_id=discord_id, active_team_id=view.selected.id))
    await session.commit()

    if require_role:
        return await _check_role(session, interaction, view.selected, discord_id, require_role)
    return view.selected


async def get_member_role(session: AsyncSession, team_id: int, discord_id: int) -> TeamRole | None:
    result = await session.execute(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.discord_id == discord_id))
    member = result.scalar_one_or_none()
    return member.role if member else None


ROLE_HIERARCHY = {
    TeamRole.PLAYER:  0,
    TeamRole.COACH:   1,
    TeamRole.MANAGER: 2,
    TeamRole.OWNER:   3,
}


async def _check_role(session, interaction, team, discord_id, required):
    role = await get_member_role(session, team.id, discord_id)
    if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
        return team
    if role is None or ROLE_HIERARCHY.get(role, -1) < ROLE_HIERARCHY.get(required, 99):
        await _send_error(
            interaction, "Permission",
            t("general.permission_denied", interaction, required=required.value, team=team.name, current=role.value if role else "none")
        )
        return None
    return team


async def _send_error(interaction, title, description):
    embed = discord.Embed(title=f"❌  {title}", description=description, color=0xBD3944)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception:
        pass
