"""
Panels cog — Embeds interactifs persistants avec boutons et modals.

Chaque channel de l'équipe reçoit un "panel" — un embed épinglé avec des
boutons qui permettent de faire les actions sans taper de commande.

Structure :
  #roster     → RosterPanel    (voir roster, ajouter/retirer un joueur)
  #calendrier → CalendarPanel  (voir events, ajouter un event)
  #praccs     → PraccPanel     (voir résultats, logger une pracc)

Les views sont persistantes (custom_id stable) : elles survivent au redémarrage
du bot grâce à bot.add_view() dans setup().

Commands :
  /panel setup [team]   → crée/recrée les panels dans tous les channels
  /panel refresh [team] → rafraîchit les panels existants
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands
from sqlalchemy import select

from config import config
from database import (
    get_session, Team, TeamChannels, Player, Event,
    EventType, MatchResult, TeamRole, TeamMember,
)
from utils.cog_helpers import get_team_for_command
from utils.team_resolver import get_member_role
from utils.channels import get_team_channels

logger = logging.getLogger(__name__)

# ── Modals ────────────────────────────────────────────────────────────────────

class AddPlayerModal(ui.Modal, title="➕  Add a player"):
    ign  = ui.TextInput(label="Riot ID",   placeholder="TenZ",  max_length=50)
    tag  = ui.TextInput(label="Riot Tag",  placeholder="EU1",   max_length=10)
    role = ui.TextInput(label="Role",      placeholder="IGL, Duelist, Initiator, Sentinel, Controller, Coach", required=False, max_length=30)

    def __init__(self, team: Team) -> None:
        super().__init__()
        self.team = team

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            # Check if already in roster
            existing = await session.execute(
                select(Player).where(
                    Player.discord_id == interaction.user.id,
                    Player.team_id == self.team.id,
                )
            )
            if existing.scalar_one_or_none():
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"⚠️  You're already in **{self.team.name}**'s roster.",
                        color=0xF0A500,
                    ),
                    ephemeral=True,
                )
                return

            player = Player(
                discord_id=interaction.user.id,
                team_id=self.team.id,
                ign=self.ign.value.strip(),
                tag=self.tag.value.strip(),
                role=self.role.value.strip() or None,
            )
            session.add(player)
            await session.commit()

            # Auto-assign role
            from utils.channels import get_team_channels, auto_assign_role
            tc = await get_team_channels(session, self.team.id)
            if tc and isinstance(interaction.user, discord.Member):
                await auto_assign_role(interaction.guild, interaction.user, tc, is_staff=False)

            # Post to #roster
            from utils.poster import post_roster_update
            if interaction.guild:
                await post_roster_update(
                    interaction.guild, session, self.team,
                    "added", self.ign.value, self.role.value or None,
                    interaction.user,
                )

            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"✅  **{self.ign.value}#{self.tag.value}** added to **{self.team.name}**!",
                    color=0x00C17C,
                ),
                ephemeral=True,
            )


class LogPraccModal(ui.Modal, title="🥊  Log a Pracc"):
    opponent   = ui.TextInput(label="Opponent",    placeholder="Team Vitality", max_length=100)
    map_played = ui.TextInput(label="Map",         placeholder="Ascent, Bind, Haven...", max_length=50)
    score      = ui.TextInput(label="Score (W/L)", placeholder="13-7 (leave blank if pending)", required=False, max_length=10)
    notes      = ui.TextInput(label="Notes",       placeholder="What went well / to improve", required=False, style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, team: Team) -> None:
        super().__init__()
        self.team = team

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Parse score
        rounds_won = rounds_lost = None
        result = MatchResult.PENDING
        score_str = self.score.value.strip()
        if score_str:
            try:
                parts = score_str.replace(" ", "").replace("-", " ").split()
                if len(parts) == 2:
                    rounds_won  = int(parts[0])
                    rounds_lost = int(parts[1])
                    result = MatchResult.WIN if rounds_won > rounds_lost else MatchResult.LOSS
            except ValueError:
                pass

        async for session in get_session():
            event = Event(
                team_id=self.team.id,
                event_type=EventType.PRACC,
                title=f"vs {self.opponent.value}",
                opponent=self.opponent.value.strip(),
                map_played=self.map_played.value.strip(),
                scheduled_at=datetime.utcnow(),
                result=result,
                rounds_won=rounds_won,
                rounds_lost=rounds_lost,
                notes=self.notes.value.strip() or None,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)

            from utils.poster import post_pracc_result
            if interaction.guild:
                await post_pracc_result(interaction.guild, session, self.team, event, interaction.user)

        score_display = f"**{rounds_won}–{rounds_lost}**" if rounds_won is not None else "*(pending)*"
        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅  Pracc vs **{self.opponent.value}** logged!  {score_display}",
                color=0x00C17C,
            ),
            ephemeral=True,
        )


class AddEventModal(ui.Modal, title="📅  Add an Event"):
    title_input = ui.TextInput(label="Title",    placeholder="vs NaVi — Pracc", max_length=100)
    date_input  = ui.TextInput(label="Date",     placeholder="DD/MM/YYYY", max_length=10)
    time_input  = ui.TextInput(label="Time",     placeholder="20:00", max_length=5)
    opponent    = ui.TextInput(label="Opponent", placeholder="Team name (optional)", required=False, max_length=100)
    notes       = ui.TextInput(label="Notes",    placeholder="Optional notes", required=False, style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, team: Team, event_type: EventType = EventType.PRACC) -> None:
        super().__init__()
        self.team       = team
        self.event_type = event_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        # Parse date + time
        try:
            dt_str = f"{self.date_input.value.strip()} {self.time_input.value.strip()}"
            scheduled_at = datetime.strptime(dt_str, "%d/%m/%Y %H:%M")
        except ValueError:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="❌  Invalid date/time format. Use DD/MM/YYYY and HH:MM.",
                    color=0xBD3944,
                ),
                ephemeral=True,
            )
            return

        async for session in get_session():
            event = Event(
                team_id=self.team.id,
                event_type=self.event_type,
                title=self.title_input.value.strip(),
                opponent=self.opponent.value.strip() or None,
                scheduled_at=scheduled_at,
                notes=self.notes.value.strip() or None,
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)

            from utils.poster import post_event_added
            if interaction.guild:
                await post_event_added(interaction.guild, session, self.team, event, interaction.user)

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅  **{self.title_input.value}** added to the calendar!",
                color=0x00C17C,
            ),
            ephemeral=True,
        )


# ── Roster Panel View ─────────────────────────────────────────────────────────

class RosterPanelView(ui.View):
    """Persistent view for #roster panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(label="View Roster", style=discord.ButtonStyle.secondary, emoji="👥", custom_id="panel:roster:view")
    async def view_roster(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            players_r = await session.execute(
                select(Player).where(Player.team_id == team.id, Player.is_active == True)
            )
            players = players_r.scalars().all()
            embed = discord.Embed(title=f"👥  {team.name} — Roster", color=0xFF4655)
            if not players:
                embed.description = "*No players yet.*"
            else:
                lines = [
                    f"{'`' + p.role + '`' if p.role else ''} **{p.ign}**#{p.tag or '???'}"
                    for p in players
                ]
                embed.description = "\n".join(lines)
            embed.set_footer(text=f"{len(players)} player(s)")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="Join Roster", style=discord.ButtonStyle.success, emoji="➕", custom_id="panel:roster:add")
    async def add_self(self, interaction: discord.Interaction, button: ui.Button) -> None:
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            await interaction.response.send_modal(AddPlayerModal(team))

    @ui.button(label="My Profile", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="panel:roster:profile")
    async def my_profile(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            player_r = await session.execute(
                select(Player).where(
                    Player.discord_id == interaction.user.id,
                    Player.team_id == team.id,
                )
            )
            player = player_r.scalar_one_or_none()
            if not player:
                await interaction.followup.send(
                    embed=discord.Embed(description="You're not in this team's roster.", color=0xBD3944),
                    ephemeral=True,
                )
                return
            embed = discord.Embed(title=f"🎮  {player.ign}#{player.tag or '???'}", color=0xFF4655)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="Role",   value=f"`{player.role or 'N/A'}`", inline=True)
            embed.add_field(name="Team",   value=f"**{team.name}**",           inline=True)
            embed.add_field(name="Joined", value=f"<t:{int(player.joined_at.timestamp())}:D>", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)


# ── Calendar Panel View ───────────────────────────────────────────────────────

class CalendarPanelView(ui.View):
    """Persistent view for #calendar panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(label="Upcoming Events", style=discord.ButtonStyle.secondary, emoji="📅", custom_id="panel:cal:view")
    async def view_events(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            now = datetime.utcnow()
            events_r = await session.execute(
                select(Event)
                .where(Event.team_id == team.id, Event.scheduled_at >= now)
                .order_by(Event.scheduled_at)
                .limit(10)
            )
            events = events_r.scalars().all()
            embed = discord.Embed(title=f"📅  {team.name} — Upcoming", color=0xFF4655)
            type_icons = {"pracc": "🥊", "official": "🏆", "meeting": "📋"}
            if not events:
                embed.description = "*No upcoming events.*"
            else:
                lines = []
                for ev in events:
                    icon = type_icons.get(ev.event_type.value, "📅")
                    opp = f" vs **{ev.opponent}**" if ev.opponent else ""
                    lines.append(f"{icon} <t:{int(ev.scheduled_at.timestamp())}:d> <t:{int(ev.scheduled_at.timestamp())}:t>{opp}")
                embed.description = "\n".join(lines)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="Add Pracc", style=discord.ButtonStyle.success, emoji="🥊", custom_id="panel:cal:add_pracc")
    async def add_pracc(self, interaction: discord.Interaction, button: ui.Button) -> None:
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            role = await get_member_role(session, team.id, interaction.user.id)
            if not _is_staff(interaction, role):
                await interaction.response.send_message(
                    embed=discord.Embed(description="❌  Staff only.", color=0xBD3944),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(AddEventModal(team, EventType.PRACC))

    @ui.button(label="Add Official", style=discord.ButtonStyle.primary, emoji="🏆", custom_id="panel:cal:add_official")
    async def add_official(self, interaction: discord.Interaction, button: ui.Button) -> None:
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            role = await get_member_role(session, team.id, interaction.user.id)
            if not _is_staff(interaction, role):
                await interaction.response.send_message(
                    embed=discord.Embed(description="❌  Staff only.", color=0xBD3944),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(AddEventModal(team, EventType.OFFICIAL))


# ── Pracc Panel View ──────────────────────────────────────────────────────────

class PraccPanelView(ui.View):
    """Persistent view for #praccs panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(label="Log Result", style=discord.ButtonStyle.success, emoji="🥊", custom_id="panel:pracc:log")
    async def log_pracc(self, interaction: discord.Interaction, button: ui.Button) -> None:
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            role = await get_member_role(session, team.id, interaction.user.id)
            if not _is_staff(interaction, role):
                await interaction.response.send_message(
                    embed=discord.Embed(description="❌  Staff only.", color=0xBD3944),
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(LogPraccModal(team))

    @ui.button(label="History", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="panel:pracc:history")
    async def history(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            events_r = await session.execute(
                select(Event)
                .where(Event.team_id == team.id, Event.event_type == EventType.PRACC)
                .order_by(Event.scheduled_at.desc())
                .limit(10)
            )
            events = events_r.scalars().all()
            embed = discord.Embed(title=f"🥊  {team.name} — Recent Praccs", color=0xFF4655)
            result_icons = {MatchResult.WIN: "🟢", MatchResult.LOSS: "🔴", MatchResult.DRAW: "🟡", MatchResult.PENDING: "⏳"}
            if not events:
                embed.description = "*No praccs recorded yet.*"
            else:
                lines = []
                for ev in events:
                    icon = result_icons.get(ev.result, "⏳")
                    score = f" `{ev.rounds_won}–{ev.rounds_lost}`" if ev.rounds_won is not None else ""
                    opp = ev.opponent or "???"
                    map_tag = f" · {ev.map_played}" if ev.map_played else ""
                    lines.append(f"{icon} vs **{opp}**{score}{map_tag}")
                embed.description = "\n".join(lines)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @ui.button(label="Map Stats", style=discord.ButtonStyle.primary, emoji="🗺️", custom_id="panel:pracc:mapstats")
    async def map_stats(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            team = await _team_from_channel(session, interaction)
            if not team:
                return
            events_r = await session.execute(
                select(Event).where(
                    Event.team_id == team.id,
                    Event.result != MatchResult.PENDING,
                    Event.map_played != None,
                )
            )
            events = events_r.scalars().all()
            if not events:
                await interaction.followup.send(
                    embed=discord.Embed(description="No map data yet.", color=0xFF4655),
                    ephemeral=True,
                )
                return
            from collections import defaultdict
            map_data: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
            for ev in events:
                if ev.result == MatchResult.WIN:
                    map_data[ev.map_played]["wins"] += 1
                elif ev.result == MatchResult.LOSS:
                    map_data[ev.map_played]["losses"] += 1

            embed = discord.Embed(title=f"🗺️  {team.name} — Map Stats", color=0xFF4655)
            lines = []
            for map_name, d in sorted(map_data.items(), key=lambda x: x[1]["wins"]/(x[1]["wins"]+x[1]["losses"]+0.001), reverse=True):
                total = d["wins"] + d["losses"]
                wr = round(d["wins"] / total * 100) if total else 0
                bar = "█" * round(wr/10) + "░" * (10 - round(wr/10))
                lines.append(f"**{map_name}** `{d['wins']}W/{d['losses']}L` {bar} `{wr}%`")
            embed.description = "\n".join(lines)
            await interaction.followup.send(embed=embed, ephemeral=True)


# ── Panel embed builders ──────────────────────────────────────────────────────

def _roster_panel_embed(team: Team) -> discord.Embed:
    embed = discord.Embed(
        title="👥  Roster",
        description=(
            "**Manage your team roster.**\n\n"
            "• **View Roster** — see all current players\n"
            "• **Join Roster** — add yourself with your Riot ID\n"
            "• **My Profile** — view your player card\n\n"
            "*Staff can add/remove any player with `/roster add` and `/roster remove`.*"
        ),
        color=0xFF4655,
    )
    embed.set_footer(text=f"{config.bot_name} · {team.name}")
    return embed


def _calendar_panel_embed(team: Team) -> discord.Embed:
    embed = discord.Embed(
        title="📅  Calendar",
        description=(
            "**Schedule and track team events.**\n\n"
            "• **Upcoming Events** — see what's next\n"
            "• **Add Pracc** — schedule a scrim *(staff)*\n"
            "• **Add Official** — schedule a match *(staff)*\n\n"
            "*The bot posts updates here automatically.*"
        ),
        color=0xFF4655,
    )
    embed.set_footer(text=f"{config.bot_name} · {team.name}")
    return embed


def _pracc_panel_embed(team: Team) -> discord.Embed:
    embed = discord.Embed(
        title="🥊  Praccs",
        description=(
            "**Track scrim results and performance.**\n\n"
            "• **Log Result** — add a pracc result *(staff)*\n"
            "• **History** — last 10 praccs\n"
            "• **Map Stats** — winrate per map\n\n"
            "*Results are automatically posted here after each log.*"
        ),
        color=0xFF4655,
    )
    embed.set_footer(text=f"{config.bot_name} · {team.name}")
    return embed


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _team_from_channel(session, interaction: discord.Interaction) -> Team | None:
    """Resolve team from the current channel's TeamChannels record."""
    if not interaction.channel_id:
        return None
    ch_id = interaction.channel_id

    # Look for a TeamChannels entry that contains this channel
    tc_r = await session.execute(select(TeamChannels).where(
        (TeamChannels.ch_roster       == ch_id) |
        (TeamChannels.ch_calendar     == ch_id) |
        (TeamChannels.ch_praccs       == ch_id) |
        (TeamChannels.ch_announcements== ch_id) |
        (TeamChannels.ch_general      == ch_id) |
        (TeamChannels.ch_staff_general== ch_id) |
        (TeamChannels.ch_mood         == ch_id) |
        (TeamChannels.ch_logs         == ch_id)
    ))
    tc = tc_r.scalar_one_or_none()
    if not tc:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="❌  This panel is not linked to a team. Use `/panel setup` to recreate it.",
                color=0xBD3944,
            ),
            ephemeral=True,
        )
        return None

    team_r = await session.execute(select(Team).where(Team.id == tc.team_id))
    return team_r.scalar_one_or_none()


def _is_staff(interaction: discord.Interaction, role) -> bool:
    if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator:
        return True
    return role in (TeamRole.OWNER, TeamRole.MANAGER, TeamRole.COACH)


# ── Cog ───────────────────────────────────────────────────────────────────────

class PanelsCog(commands.Cog, name="Panels"):
    """Panels interactifs persistants."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Register persistent views so they survive restarts
        self.bot.add_view(RosterPanelView())
        self.bot.add_view(CalendarPanelView())
        self.bot.add_view(PraccPanelView())
        logger.info("Persistent panel views registered.")

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

    panel = app_commands.Group(name="panel", description="Interactive panel management")

    @panel.command(name="setup", description="Create interactive panels in all team channels · Staff only")
    @app_commands.describe(team_name="Team · Équipe (optional)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def setup(self, interaction: discord.Interaction, team_name: Optional[str] = None) -> None:
        await interaction.response.defer(ephemeral=True)

        async for session in get_session():
            from utils.team_resolver import resolve_team
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team:
                return

            tc = await get_team_channels(session, team.id)
            if not tc:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description="❌  No channels found for this team. Run `/team setup-channels` first.",
                        color=0xBD3944,
                    ),
                    ephemeral=True,
                )
                return

            created = 0
            errors  = 0

            panels = [
                (tc.ch_roster,   _roster_panel_embed(team),   RosterPanelView()),
                (tc.ch_calendar, _calendar_panel_embed(team), CalendarPanelView()),
                (tc.ch_praccs,   _pracc_panel_embed(team),    PraccPanelView()),
            ]

            panel_msg_fields = ["panel_roster_msg", "panel_calendar_msg", "panel_praccs_msg"]

            for (ch_id, embed, view), msg_field in zip(panels, panel_msg_fields):
                ch = interaction.guild.get_channel(ch_id) if ch_id else None
                if not isinstance(ch, discord.TextChannel):
                    errors += 1
                    continue

                # Delete old panel message if exists
                old_msg_id = getattr(tc, msg_field, None)
                if old_msg_id:
                    try:
                        old_msg = await ch.fetch_message(old_msg_id)
                        await old_msg.delete()
                    except (discord.NotFound, discord.HTTPException):
                        pass

                try:
                    msg = await ch.send(embed=embed, view=view)
                    await msg.pin()
                    setattr(tc, msg_field, msg.id)
                    created += 1
                except discord.HTTPException as e:
                    logger.error(f"Failed to post panel in #{ch.name}: {e}")
                    errors += 1

            await session.commit()

            status = f"✅  **{created}** panel(s) created"
            if errors:
                status += f"  ·  ⚠️  {errors} channel(s) unreachable"
            await interaction.followup.send(
                embed=discord.Embed(description=status, color=0x00C17C if not errors else 0xF0A500),
                ephemeral=True,
            )

    @panel.command(name="refresh", description="Refresh all panel embeds · Staff only")
    @app_commands.describe(team_name="Team · Équipe (optional)")
    @app_commands.autocomplete(team_name=team_autocomplete)
    async def refresh(self, interaction: discord.Interaction, team_name: Optional[str] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        async for session in get_session():
            from utils.team_resolver import resolve_team
            team = await resolve_team(
                session, interaction.guild_id, interaction.user.id,
                interaction, team_name, require_role=TeamRole.MANAGER,
            )
            if not team:
                return

            tc = await get_team_channels(session, team.id)
            if not tc:
                await interaction.followup.send(
                    embed=discord.Embed(description="❌  No channels set up.", color=0xBD3944),
                    ephemeral=True,
                )
                return

            panels = [
                (tc.ch_roster,   tc.panel_roster_msg,   _roster_panel_embed(team),   RosterPanelView()),
                (tc.ch_calendar, tc.panel_calendar_msg, _calendar_panel_embed(team), CalendarPanelView()),
                (tc.ch_praccs,   tc.panel_praccs_msg,   _pracc_panel_embed(team),    PraccPanelView()),
            ]

            refreshed = 0
            for ch_id, msg_id, embed, view in panels:
                ch = interaction.guild.get_channel(ch_id) if ch_id else None
                if not isinstance(ch, discord.TextChannel) or not msg_id:
                    continue
                try:
                    msg = await ch.fetch_message(msg_id)
                    await msg.edit(embed=embed, view=view)
                    refreshed += 1
                except (discord.NotFound, discord.HTTPException):
                    pass

            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"✅  **{refreshed}** panel(s) refreshed.",
                    color=0x00C17C,
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelsCog(bot))
