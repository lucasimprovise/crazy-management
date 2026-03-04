"""
Helpers partagés entre les cogs pour éviter la duplication.
Centralise : team autocomplete, team resolution wrapper.
"""
from __future__ import annotations
from typing import Optional

import discord
from discord import app_commands
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Team, TeamRole
from utils.team_resolver import resolve_team as _resolve_team


async def team_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete commun pour le paramètre team_name."""
    from database import get_session
    async for session in get_session():
        r = await session.execute(
            select(Team).where(Team.guild_id == interaction.guild_id, Team.is_active == True)
        )
        return [
            app_commands.Choice(name=t.name + (f" [{t.tag}]" if t.tag else ""), value=t.name)
            for t in r.scalars().all()
            if current.lower() in t.name.lower()
        ][:25]
    return []


async def get_team_for_command(
    session: AsyncSession,
    interaction: discord.Interaction,
    team_name: Optional[str] = None,
    require_role: Optional[TeamRole] = None,
) -> Team | None:
    """Wrapper court pour resolve_team utilisable dans les cogs."""
    return await _resolve_team(
        session=session,
        guild_id=interaction.guild_id,
        discord_id=interaction.user.id,
        interaction=interaction,
        team_name=team_name,
        require_role=require_role,
    )
