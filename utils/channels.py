"""
channels.py — Création et gestion automatique des channels Discord par équipe.

Quand une équipe est créée avec /team create, ce module :
  1. Crée deux rôles Discord  (@<TAG> Staff  /  @<TAG> Player)
  2. Crée une catégorie publique  ═══〔🎮 TEAM〕═══
     ├── 📢 annonces      (lecture seule joueurs — le bot poste ici)
     ├── 📅 calendrier    (lecture seule — le bot poste les events)
     ├── 👥 roster        (lecture seule — le bot poste les updates roster)
     ├── 🥊 praccs        (lecture seule — résultats + stats)
     └── 💬 général       (tout le monde peut écrire)
  3. Crée une catégorie privée  ═══〔🔒 TEAM Staff〕═══
     ├── 🎙️ staff-général  (staff uniquement)
     ├── 💬 mood-overview  (le bot poste le mood hebdo)
     └── 📋 logs           (toutes les actions bot)

Les IDs sont sauvegardés en DB (TeamChannels) pour que le bot puisse
poster automatiquement dans les bons channels.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from config import config
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import Team, TeamChannels

logger = logging.getLogger(__name__)


# ── Channel structure definition ─────────────────────────────────────────────

PUBLIC_CHANNELS = [
    {
        "key":   "ch_announcements",
        "name":  "📢・annonces",
        "topic": "Annonces officielles de l'équipe · Official team announcements",
        "readonly_for_players": True,
    },
    {
        "key":   "ch_calendar",
        "name":  "📅・calendrier",
        "topic": "Calendrier automatique des events · Auto-updated event calendar",
        "readonly_for_players": True,
    },
    {
        "key":   "ch_roster",
        "name":  "👥・roster",
        "topic": "Roster de l'équipe mis à jour automatiquement · Auto-updated roster",
        "readonly_for_players": True,
    },
    {
        "key":   "ch_praccs",
        "name":  "🥊・praccs",
        "topic": "Résultats et stats des praccs · Pracc results and stats",
        "readonly_for_players": True,
    },
    {
        "key":   "ch_general",
        "name":  "💬・général",
        "topic": "Discussion générale de l'équipe · General team discussion",
        "readonly_for_players": False,
    },
]

STAFF_CHANNELS = [
    {
        "key":   "ch_staff_general",
        "name":  "🎙️・staff-général",
        "topic": "Canal privé staff · Private staff channel",
    },
    {
        "key":   "ch_mood",
        "name":  "💬・mood-overview",
        "topic": "Mood hebdomadaire de l'équipe (posté automatiquement) · Weekly team mood",
    },
    {
        "key":   "ch_logs",
        "name":  "📋・logs-bot",
        "topic": "Journal des actions du bot · Bot action logs",
    },
]


async def setup_team_channels(
    guild: discord.Guild,
    team: Team,
    session: AsyncSession,
) -> TeamChannels | None:
    """
    Crée toute la structure de channels pour une équipe.
    Retourne le TeamChannels créé, ou None en cas d'erreur de permissions.
    """
    tag = team.tag or team.name[:3].upper()
    name = team.name

    try:
        # ── 1. Create roles ───────────────────────────────────────────────────
        staff_role = await guild.create_role(
            name=f"{tag} Staff",
            color=discord.Color.from_str("#FF4655"),
            hoist=True,   # Affiché séparément dans la liste des membres
            mentionable=True,
            reason=f"{config.bot_name} — équipe {name}",
        )
        player_role = await guild.create_role(
            name=f"{tag} Player",
            color=discord.Color.from_str("#7289DA"),
            hoist=True,
            mentionable=True,
            reason=f"{config.bot_name} — équipe {name}",
        )
        logger.info(f"Rôles créés : @{staff_role.name}, @{player_role.name}")

        # ── 2. Public category ────────────────────────────────────────────────
        # Everyone can see, players read-only on most channels
        public_overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,   # Default: read-only
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            ),
            player_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,   # Overridden for #général below
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                manage_messages=True,
            ),
        }

        public_cat = await guild.create_category(
            name=f"〔🎮〕 {name}",
            overwrites=public_overwrites,
            reason=f"{config.bot_name} — équipe {name}",
        )
        logger.info(f"Catégorie publique créée : {public_cat.name}")

        # ── 3. Staff category ─────────────────────────────────────────────────
        staff_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            staff_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                manage_messages=True,
            ),
        }

        staff_cat = await guild.create_category(
            name=f"〔🔒〕 {name} Staff",
            overwrites=staff_overwrites,
            reason=f"{config.bot_name} — équipe {name}",
        )
        logger.info(f"Catégorie staff créée : {staff_cat.name}")

        # ── 4. Create public channels ─────────────────────────────────────────
        channel_ids: dict[str, int] = {}

        for ch_def in PUBLIC_CHANNELS:
            # #général : players can write
            extra_overwrites = {}
            if not ch_def["readonly_for_players"]:
                extra_overwrites[player_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                )

            ch = await guild.create_text_channel(
                name=ch_def["name"],
                category=public_cat,
                topic=ch_def["topic"],
                overwrites={**public_overwrites, **extra_overwrites} if extra_overwrites else public_overwrites,
                reason=f"{config.bot_name} — équipe {name}",
            )
            channel_ids[ch_def["key"]] = ch.id
            logger.info(f"  Channel créé : #{ch.name}")

        # ── 5. Create staff channels ──────────────────────────────────────────
        for ch_def in STAFF_CHANNELS:
            ch = await guild.create_text_channel(
                name=ch_def["name"],
                category=staff_cat,
                topic=ch_def["topic"],
                overwrites=staff_overwrites,
                reason=f"{config.bot_name} — équipe {name}",
            )
            channel_ids[ch_def["key"]] = ch.id
            logger.info(f"  Channel staff créé : #{ch.name}")

        # ── 6. Save to DB ─────────────────────────────────────────────────────
        team_channels = TeamChannels(
            team_id           = team.id,
            category_id       = public_cat.id,
            staff_category_id = staff_cat.id,
            role_staff_id     = staff_role.id,
            role_player_id    = player_role.id,
            **channel_ids,
        )
        session.add(team_channels)
        await session.commit()

        logger.info(f"TeamChannels sauvegardé pour l'équipe {name} (id={team.id})")
        return team_channels

    except discord.Forbidden:
        logger.error(f"Permission refusée lors de la création des channels pour {name}.")
        return None
    except discord.HTTPException as e:
        logger.error(f"Erreur Discord lors de la création des channels : {e}")
        return None


async def delete_team_channels(
    guild: discord.Guild,
    team: Team,
    session: AsyncSession,
) -> bool:
    """
    Supprime tous les channels, catégories et rôles liés à une équipe.
    Appelé lors de /team delete.
    """
    result = await session.execute(
        select(TeamChannels).where(TeamChannels.team_id == team.id)
    )
    tc = result.scalar_one_or_none()
    if not tc:
        return True  # Rien à supprimer

    deleted = 0

    async def _delete_channel(channel_id: Optional[int]) -> None:
        nonlocal deleted
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if ch:
            try:
                await ch.delete(reason=f"{config.bot_name} — suppression équipe {team.name}")
                deleted += 1
            except discord.HTTPException:
                pass

    async def _delete_role(role_id: Optional[int]) -> None:
        if not role_id:
            return
        role = guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason=f"{config.bot_name} — suppression équipe {team.name}")
            except discord.HTTPException:
                pass

    # Delete all text channels first
    for ch_def in PUBLIC_CHANNELS + STAFF_CHANNELS:
        await _delete_channel(getattr(tc, ch_def["key"], None))

    # Then categories
    await _delete_channel(tc.category_id)
    await _delete_channel(tc.staff_category_id)

    # Then roles
    await _delete_role(tc.role_staff_id)
    await _delete_role(tc.role_player_id)

    await session.delete(tc)
    await session.commit()

    logger.info(f"Supprimé {deleted} channels pour l'équipe {team.name}")
    return True


async def get_team_channels(session: AsyncSession, team_id: int) -> TeamChannels | None:
    """Récupère le TeamChannels d'une équipe."""
    result = await session.execute(
        select(TeamChannels).where(TeamChannels.team_id == team_id)
    )
    return result.scalar_one_or_none()


async def auto_assign_role(
    guild: discord.Guild,
    member: discord.Member,
    team_channels: TeamChannels,
    is_staff: bool,
) -> None:
    """
    Assigne automatiquement le rôle @TAG Staff ou @TAG Player
    à un membre Discord quand il est ajouté à l'équipe.
    """
    role_id = team_channels.role_staff_id if is_staff else team_channels.role_player_id
    if not role_id:
        return
    role = guild.get_role(role_id)
    if role and role not in member.roles:
        try:
            await member.add_roles(role, reason=f"{config.bot_name} — ajout roster")
        except discord.Forbidden:
            logger.warning(f"Impossible d'assigner le rôle {role.name} à {member.display_name}")


async def auto_remove_role(
    guild: discord.Guild,
    member: discord.Member,
    team_channels: TeamChannels,
) -> None:
    """Retire les rôles équipe d'un membre retiré du roster."""
    for role_id in (team_channels.role_staff_id, team_channels.role_player_id):
        if not role_id:
            continue
        role = guild.get_role(role_id)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"{config.bot_name} — retrait roster")
            except discord.Forbidden:
                pass
