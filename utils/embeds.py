"""
Centralized embed builders for a consistent Valorant-themed UI.
Palette: dark background · #FF4655 (Valorant red) · #ECE8E1 (off-white) · #BD3944 (deep red)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord
from config import config

# ── Colour palette ────────────────────────────────────────────────────────────
COLOR_PRIMARY = 0xFF4655   # Valorant red
COLOR_SUCCESS = 0x00C17C   # Green
COLOR_WARNING = 0xF0A500   # Amber
COLOR_ERROR   = 0xBD3944   # Deep red
COLOR_INFO    = 0x7289DA   # Discord blurple (neutral)
COLOR_NEUTRAL = 0x2F3136   # Dark grey


def _base(title: str, color: int, description: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.utcnow()
    return embed


# ── Generic ───────────────────────────────────────────────────────────────────

def success(title: str, description: str = "") -> discord.Embed:
    return _base(f"✅  {title}", COLOR_SUCCESS, description)


def error(title: str, description: str = "") -> discord.Embed:
    return _base(f"❌  {title}", COLOR_ERROR, description)


def warning(title: str, description: str = "") -> discord.Embed:
    return _base(f"⚠️  {title}", COLOR_WARNING, description)


def info(title: str, description: str = "") -> discord.Embed:
    return _base(f"ℹ️  {title}", COLOR_INFO, description)


# ── Roster ────────────────────────────────────────────────────────────────────

def roster_embed(team_name: str, players: list) -> discord.Embed:
    embed = _base(f"🎮  Roster — {team_name}", COLOR_PRIMARY)
    embed.description = f"**{len(players)}** joueur(s) enregistré(s)\n\u200b"

    staff = [p for p in players if p.is_staff]
    active = [p for p in players if not p.is_staff and p.is_active]

    if staff:
        embed.add_field(
            name="🎙️  Staff",
            value="\n".join(f"`{p.role or 'Staff'}` · **{p.ign}**" for p in staff),
            inline=False,
        )
    if active:
        embed.add_field(
            name="⚔️  Joueurs",
            value="\n".join(f"`{p.role or 'N/A'}` · **{p.ign}**" for p in active),
            inline=False,
        )

    embed.set_footer(text=config.bot_name)
    return embed


# ── Availability ──────────────────────────────────────────────────────────────

SLOT_LABELS = {
    "morning":   "🌅 Matin (08h–12h)",
    "afternoon": "☀️ Après-midi (12h–18h)",
    "evening":   "🌙 Soir (18h–23h)",
    "all_day":   "📅 Toute la journée",
}


def availability_week_embed(
    team_name: str,
    week_data: dict[str, dict[str, list[str]]],
) -> discord.Embed:
    """
    week_data format:
    {
      "Lundi 03/03": {"morning": ["Player1", "Player2"], "evening": ["Player3"]},
      ...
    }
    """
    embed = _base(f"📅  Disponibilités — {team_name}", COLOR_PRIMARY)
    embed.description = "Vue hebdomadaire de l'équipe\n\u200b"

    for day, slots in week_data.items():
        lines = []
        for slot_key, label in SLOT_LABELS.items():
            players = slots.get(slot_key, [])
            if players:
                names = ", ".join(f"**{p}**" for p in players)
                lines.append(f"{label}: {names}")
        if lines:
            embed.add_field(name=f"📆 {day}", value="\n".join(lines), inline=False)

    embed.set_footer(text="Utilisez /dispo add pour ajouter vos disponibilités")
    return embed


# ── Calendar / Events ─────────────────────────────────────────────────────────

EVENT_ICONS = {
    "pracc":         "🥊",
    "official":      "🏆",
    "meeting":       "📋",
    "scrim_request": "🤝",
}

RESULT_ICONS = {
    "win":     "🟢",
    "loss":    "🔴",
    "draw":    "🟡",
    "pending": "⏳",
}


def event_embed(event) -> discord.Embed:
    icon = EVENT_ICONS.get(event.event_type.value, "📌")
    result_icon = RESULT_ICONS.get(event.result.value if event.result else "pending", "⏳")

    color = COLOR_PRIMARY
    if event.result and event.result.value == "win":
        color = COLOR_SUCCESS
    elif event.result and event.result.value == "loss":
        color = COLOR_ERROR

    embed = _base(f"{icon}  {event.title}", color)
    embed.description = event.description or ""

    ts = int(event.scheduled_at.timestamp())
    embed.add_field(name="📅 Date", value=f"<t:{ts}:F>", inline=True)
    embed.add_field(name="🏷️ Type", value=event.event_type.value.capitalize(), inline=True)

    if event.opponent:
        embed.add_field(name="🆚 Adversaire", value=f"**{event.opponent}**", inline=True)
    if event.map_played:
        embed.add_field(name="🗺️ Map", value=event.map_played, inline=True)
    if event.result and event.result.value != "pending":
        score = ""
        if event.rounds_won is not None and event.rounds_lost is not None:
            score = f" ({event.rounds_won}–{event.rounds_lost})"
        embed.add_field(
            name="📊 Résultat",
            value=f"{result_icon} {event.result.value.capitalize()}{score}",
            inline=True,
        )
    if event.vod_url:
        embed.add_field(name="🎬 VOD", value=f"[Voir la VOD]({event.vod_url})", inline=True)
    if event.notes:
        embed.add_field(name="📝 Notes", value=event.notes, inline=False)

    embed.set_footer(text=f"ID: {event.id} · {config.bot_name}")
    return embed


def calendar_embed(team_name: str, events: list, month_label: str) -> discord.Embed:
    embed = _base(f"🗓️  Calendrier — {team_name}", COLOR_PRIMARY)
    embed.description = f"**{month_label}** · {len(events)} événement(s)\n\u200b"

    for event in events:
        icon = EVENT_ICONS.get(event.event_type.value, "📌")
        result_icon = RESULT_ICONS.get(event.result.value if event.result else "pending", "⏳")
        ts = int(event.scheduled_at.timestamp())
        opponent_str = f" vs **{event.opponent}**" if event.opponent else ""
        embed.add_field(
            name=f"{icon} {event.title}{opponent_str}",
            value=f"<t:{ts}:d> à <t:{ts}:t> · {result_icon}",
            inline=False,
        )

    embed.set_footer(text=config.bot_name)
    return embed


# ── Pracc ─────────────────────────────────────────────────────────────────────

def pracc_summary_embed(team_name: str, events: list) -> discord.Embed:
    embed = _base(f"🥊  Historique Praccs — {team_name}", COLOR_PRIMARY)

    wins = sum(1 for e in events if e.result and e.result.value == "win")
    losses = sum(1 for e in events if e.result and e.result.value == "loss")
    total = wins + losses

    if total > 0:
        wr = round(wins / total * 100)
        embed.description = f"**{wins}W / {losses}L** · Winrate : `{wr}%`\n\u200b"
    else:
        embed.description = "Aucun résultat enregistré.\n\u200b"

    for event in events[-10:]:  # last 10
        icon = RESULT_ICONS.get(event.result.value if event.result else "pending", "⏳")
        score = ""
        if event.rounds_won is not None and event.rounds_lost is not None:
            score = f"`{event.rounds_won}–{event.rounds_lost}`"
        ts = int(event.scheduled_at.timestamp())
        embed.add_field(
            name=f"{icon} {event.opponent or 'N/A'} · {event.map_played or '?'}",
            value=f"<t:{ts}:d> {score}",
            inline=True,
        )

    embed.set_footer(text=config.bot_name)
    return embed


# ── Player Stats ──────────────────────────────────────────────────────────────

def stats_embed(ign: str, data: dict) -> discord.Embed:
    embed = _base(f"📊  Stats — {ign}", COLOR_PRIMARY)

    account = data.get("account", {})
    if account.get("card"):
        embed.set_thumbnail(url=account["card"].get("small", ""))

    card = data.get("stats", {})
    embed.add_field(name="🎯 ACS moyen", value=f"`{card.get('acs', 'N/A')}`", inline=True)
    embed.add_field(name="💀 K/D/A", value=f"`{card.get('kda', 'N/A')}`", inline=True)
    embed.add_field(name="🔫 HS%", value=f"`{card.get('hs_percent', 'N/A')}%`", inline=True)
    embed.add_field(name="🥇 Winrate", value=f"`{card.get('winrate', 'N/A')}%`", inline=True)
    embed.add_field(name="🏅 Rang", value=f"`{card.get('rank', 'N/A')}`", inline=True)
    embed.add_field(name="🎮 Matchs", value=f"`{card.get('matches', 'N/A')}`", inline=True)

    top_agents = card.get("top_agents", [])
    if top_agents:
        embed.add_field(
            name="🦸 Top Agents",
            value=" · ".join(f"**{a}**" for a in top_agents[:3]),
            inline=False,
        )

    embed.set_footer(text=f"Données via Henrik Dev API · {config.bot_name}")
    return embed


def performance_embed(player_ign: str, perfs: list) -> discord.Embed:
    embed = _base(f"📈  Performances — {player_ign}", COLOR_PRIMARY)

    if not perfs:
        embed.description = "Aucune performance enregistrée."
        return embed

    total = len(perfs)
    avg_acs = sum(p.acs or 0 for p in perfs) / total
    avg_kd = sum((p.kills or 0) / max(p.deaths or 1, 1) for p in perfs) / total

    embed.description = (
        f"**{total}** match(s) enregistré(s)\n"
        f"ACS moyen : `{avg_acs:.0f}` · K/D moyen : `{avg_kd:.2f}`\n\u200b"
    )

    for perf in perfs[-5:]:
        embed.add_field(
            name=f"🗺️ {perf.event.map_played or 'Map inconnue'} · {perf.agent or 'N/A'}",
            value=(
                f"K/D/A : `{perf.kills}/{perf.deaths}/{perf.assists}` · "
                f"ACS : `{perf.acs or 'N/A'}` · HS : `{perf.hs_percent or 'N/A'}%`"
            ),
            inline=False,
        )

    embed.set_footer(text=config.bot_name)
    return embed
