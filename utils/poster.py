"""
poster.py — Auto-post visual feedback to team channels.

After every significant action (roster change, pracc logged, event added...),
the bot automatically updates the relevant channel with a fresh embed.

This creates a "live dashboard" feel without anyone having to type a command.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    Team, TeamChannels, Player, Event, EventType, MatchResult,
    TeamMood, MoodRating,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

COLOR_PRIMARY = 0xFF4655
COLOR_SUCCESS = 0x00C17C
COLOR_NEUTRAL = 0x2B2D31


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_tc(session: AsyncSession, team_id: int) -> TeamChannels | None:
    r = await session.execute(
        select(TeamChannels).where(TeamChannels.team_id == team_id)
    )
    return r.scalar_one_or_none()


async def _get_channel(guild: discord.Guild, channel_id: int | None) -> discord.TextChannel | None:
    if not channel_id:
        return None
    ch = guild.get_channel(channel_id)
    return ch if isinstance(ch, discord.TextChannel) else None


async def _safe_post(channel: discord.TextChannel, embed: discord.Embed) -> discord.Message | None:
    """Post an embed, return the message or None on failure."""
    try:
        return await channel.send(embed=embed)
    except discord.Forbidden:
        logger.warning(f"Cannot post to #{channel.name} — missing permissions.")
    except discord.HTTPException as e:
        logger.error(f"Failed to post to #{channel.name}: {e}")
    return None


# ── Roster post ───────────────────────────────────────────────────────────────

async def post_roster_update(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
    action: str,         # "added" | "removed"
    player_ign: str,
    player_role: str | None,
    actor: discord.Member,
) -> None:
    """Post a roster update + full refreshed roster to #roster."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_roster if tc else None)
    if not ch:
        return

    # Action notice
    action_embed = discord.Embed(
        title="👥  Roster Update",
        color=COLOR_SUCCESS if action == "added" else 0xBD3944,
    )
    icon = "✅" if action == "added" else "❌"
    verb = "added to" if action == "added" else "removed from"
    action_embed.description = (
        f"{icon}  **{player_ign}**"
        + (f"  ·  `{player_role}`" if player_role else "")
        + f"  has been {verb} **{team.name}**"
    )
    action_embed.set_footer(text=f"By {actor.display_name}  ·  {team.name}")
    action_embed.timestamp = datetime.utcnow()
    await _safe_post(ch, action_embed)

    # Full roster refresh
    players_r = await session.execute(
        select(Player).where(Player.team_id == team.id, Player.is_active == True)
    )
    players = players_r.scalars().all()

    roster_embed = discord.Embed(
        title=f"👥  {team.name} — Current Roster",
        color=COLOR_PRIMARY,
        timestamp=datetime.utcnow(),
    )
    if not players:
        roster_embed.description = "*No players yet. Use `/roster add` or the panel buttons.*"
    else:
        role_order = ["IGL", "Duelist", "Initiator", "Sentinel", "Controller", "Coach", "Analyst"]
        def sort_key(p):
            try:
                return role_order.index(p.role) if p.role else 99
            except ValueError:
                return 99
        sorted_players = sorted(players, key=sort_key)
        lines = []
        for p in sorted_players:
            member = guild.get_member(p.discord_id)
            mention = member.mention if member else f"`{p.ign}`"
            role_tag = f"` {p.role} `" if p.role else ""
            lines.append(f"{role_tag}  **{p.ign}**#{p.tag or '???'}  ·  {mention}")
        roster_embed.description = "\n".join(lines)
    roster_embed.set_footer(text=f"{len(players)} player(s)  ·  {team.name}")
    await _safe_post(ch, roster_embed)


# ── Calendar post ─────────────────────────────────────────────────────────────

async def post_event_added(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
    event: Event,
    actor: discord.Member,
) -> None:
    """Post a new event card to #calendar."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_calendar if tc else None)
    if not ch:
        return

    type_icons = {
        EventType.PRACC:    "🥊",
        EventType.OFFICIAL: "🏆",
        EventType.MEETING:  "📋",
    }
    icon = type_icons.get(event.event_type, "📅")

    embed = discord.Embed(
        title=f"{icon}  New Event — {event.title}",
        color=COLOR_PRIMARY,
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="📅 Date", value=f"<t:{int(event.scheduled_at.timestamp())}:F>", inline=True)
    embed.add_field(name="🏷️ Type", value=event.event_type.value.capitalize(), inline=True)
    if event.opponent:
        embed.add_field(name="🆚 Opponent", value=f"**{event.opponent}**", inline=True)
    if event.map_played:
        embed.add_field(name="🗺️ Map", value=event.map_played, inline=True)
    if event.description:
        embed.add_field(name="📝 Notes", value=event.description, inline=False)
    embed.set_footer(text=f"Added by {actor.display_name}  ·  ID: {event.id}")
    await _safe_post(ch, embed)

    # Upcoming events summary (next 7 days)
    await post_upcoming_events(guild, session, team)


async def post_upcoming_events(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
) -> None:
    """Post a refreshed upcoming events list to #calendar."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_calendar if tc else None)
    if not ch:
        return

    now = datetime.utcnow()
    week_end = now + timedelta(days=14)

    events_r = await session.execute(
        select(Event)
        .where(
            Event.team_id == team.id,
            Event.scheduled_at >= now,
            Event.scheduled_at <= week_end,
        )
        .order_by(Event.scheduled_at)
        .limit(8)
    )
    events = events_r.scalars().all()

    embed = discord.Embed(
        title=f"📅  {team.name} — Next 2 Weeks",
        color=COLOR_NEUTRAL,
        timestamp=datetime.utcnow(),
    )
    if not events:
        embed.description = "*No upcoming events. Use `/cal add` or the panel to schedule one.*"
    else:
        type_icons = {
            EventType.PRACC.value:    "🥊",
            EventType.OFFICIAL.value: "🏆",
            EventType.MEETING.value:  "📋",
        }
        lines = []
        for ev in events:
            icon = type_icons.get(ev.event_type.value, "📅")
            opp = f" vs **{ev.opponent}**" if ev.opponent else ""
            ts = f"<t:{int(ev.scheduled_at.timestamp())}:d> <t:{int(ev.scheduled_at.timestamp())}:t>"
            lines.append(f"{icon} {ts}{opp}  ·  `{ev.title}`")
        embed.description = "\n".join(lines)
    embed.set_footer(text=f"{len(events)} event(s) · Updated")
    await _safe_post(ch, embed)


# ── Pracc post ────────────────────────────────────────────────────────────────

async def post_pracc_result(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
    event: Event,
    actor: discord.Member,
) -> None:
    """Post a pracc result card to #praccs."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_praccs if tc else None)
    if not ch:
        return

    result_icons = {
        MatchResult.WIN:  ("🟢", "WIN",  COLOR_SUCCESS),
        MatchResult.LOSS: ("🔴", "LOSS", 0xBD3944),
        MatchResult.DRAW: ("🟡", "DRAW", 0xF0A500),
    }
    icon, label, color = result_icons.get(event.result, ("⏳", "PENDING", COLOR_NEUTRAL))

    embed = discord.Embed(
        title=f"{icon}  {label}  —  {team.name} vs {event.opponent or '???'}",
        color=color,
        timestamp=datetime.utcnow(),
    )
    if event.rounds_won is not None and event.rounds_lost is not None:
        embed.add_field(name="📊 Score", value=f"**{event.rounds_won} – {event.rounds_lost}**", inline=True)
    if event.map_played:
        embed.add_field(name="🗺️ Map", value=event.map_played, inline=True)
    if event.vod_url:
        embed.add_field(name="🎬 VOD", value=f"[Watch]({event.vod_url})", inline=True)
    if event.notes:
        embed.add_field(name="📝 Notes", value=event.notes, inline=False)

    embed.set_footer(text=f"Logged by {actor.display_name}  ·  {team.name}")
    await _safe_post(ch, embed)

    # Refresh pracc stats summary
    await post_pracc_stats(guild, session, team)


async def post_pracc_stats(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
) -> None:
    """Post refreshed W/L stats to #praccs."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_praccs if tc else None)
    if not ch:
        return

    events_r = await session.execute(
        select(Event).where(
            Event.team_id == team.id,
            Event.event_type == EventType.PRACC,
            Event.result != MatchResult.PENDING,
        )
    )
    events = events_r.scalars().all()
    if not events:
        return

    wins   = sum(1 for e in events if e.result == MatchResult.WIN)
    losses = sum(1 for e in events if e.result == MatchResult.LOSS)
    draws  = sum(1 for e in events if e.result == MatchResult.DRAW)
    total  = wins + losses
    wr     = round(wins / total * 100) if total else 0

    bar_filled = round(wr / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)

    embed = discord.Embed(
        title=f"📊  {team.name} — Pracc Stats",
        description=f"**{wins}W  {losses}L  {draws}D**\n`{bar}`  **{wr}%** winrate",
        color=COLOR_SUCCESS if wr >= 50 else 0xBD3944,
        timestamp=datetime.utcnow(),
    )
    # Recent form (last 5)
    recent = sorted(events, key=lambda e: e.scheduled_at)[-5:]
    icons = {MatchResult.WIN: "🟢", MatchResult.LOSS: "🔴", MatchResult.DRAW: "🟡"}
    form = "  ".join(icons.get(e.result, "⚪") for e in recent)
    embed.add_field(name="Recent form", value=form or "—", inline=False)
    embed.set_footer(text=f"Based on {len(events)} pracc(s) · {team.name}")
    await _safe_post(ch, embed)


# ── Mood post ─────────────────────────────────────────────────────────────────

async def post_mood_overview(
    guild: discord.Guild,
    session: AsyncSession,
    team: Team,
    week_start: datetime,
) -> None:
    """Post/refresh weekly mood overview to #mood-overview (staff only)."""
    tc = await _get_tc(session, team.id)
    ch = await _get_channel(guild, tc.ch_mood if tc else None)
    if not ch:
        return

    moods_r = await session.execute(
        select(TeamMood).where(
            TeamMood.team_id == team.id,
            TeamMood.week_start == week_start,
        )
    )
    moods = moods_r.scalars().all()

    players_r = await session.execute(
        select(Player).where(Player.team_id == team.id, Player.is_active == True)
    )
    total_players = len(players_r.scalars().all())

    MOOD_EMOJI = {"1": "😞", "2": "😕", "3": "😐", "4": "🙂", "5": "😄"}
    MOOD_COLOR = {1: 0xBD3944, 2: 0xF0A500, 3: 0x7289DA, 4: 0x43B581, 5: 0x00C17C}

    if not moods:
        embed = discord.Embed(
            title=f"💬  Team Mood — Week of {week_start.strftime('%d/%m/%Y')}",
            description=f"No moods submitted yet  ·  `0/{total_players}` players",
            color=0x7289DA,
            timestamp=datetime.utcnow(),
        )
        await _safe_post(ch, embed)
        return

    avg = sum(int(m.rating.value) for m in moods) / len(moods)
    avg_emoji = MOOD_EMOJI.get(str(round(avg)), "😐")
    color = MOOD_COLOR.get(round(avg), 0x7289DA)

    embed = discord.Embed(
        title=f"💬  Team Mood — Week of {week_start.strftime('%d/%m/%Y')}",
        description=(
            f"**Average: {avg_emoji} `{avg:.1f}/5`**  ·  "
            f"Participation: `{len(moods)}/{total_players}`"
        ),
        color=color,
        timestamp=datetime.utcnow(),
    )

    # Distribution
    dist = {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}
    for m in moods:
        dist[m.rating.value] += 1

    dist_lines = []
    labels = {"5": "😄 Excellent", "4": "🙂 Good", "3": "😐 Neutral", "2": "😕 Bad", "1": "😞 Very bad"}
    for val in ["5", "4", "3", "2", "1"]:
        count = dist[val]
        if count:
            bar = "█" * count + "░" * (len(moods) - count)
            dist_lines.append(f"{labels[val]:<18} {bar} `{count}`")
    if dist_lines:
        embed.add_field(name="Distribution", value="\n".join(dist_lines), inline=False)

    # Individual entries with notes
    for m in sorted(moods, key=lambda x: int(x.rating.value), reverse=True):
        players_r2 = await session.execute(
            select(Player).where(Player.id == m.player_id)
        )
        player = players_r2.scalar_one_or_none()
        if not player:
            continue
        emoji = MOOD_EMOJI.get(m.rating.value, "😐")
        note = f"\n> _{m.note}_" if m.note else ""
        embed.add_field(
            name=f"{emoji}  {player.ign}",
            value=f"`{labels.get(m.rating.value, m.rating.value)}`{note}",
            inline=True,
        )

    embed.set_footer(text=f"Staff only · {team.name}")
    await _safe_post(ch, embed)
