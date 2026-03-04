"""
Database models for Esports Team Manager Bot.
Uses SQLAlchemy 2.0 async ORM.

Multi-tenant design:
  - Multiple teams per guild (org avec main + academy)
  - GuildContext: active team per user session
  - TeamMember: staff roles distinct from player roster
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, ForeignKey,
    Enum, Text, BigInteger, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    __allow_unmapped__ = True


# ── Enums ─────────────────────────────────────────────────────────────────────

class TeamRole(PyEnum):
    OWNER   = "owner"    # Créateur, droits totaux (suppression équipe)
    MANAGER = "manager"  # Gestion roster + calendrier
    COACH   = "coach"    # Lecture tout + édition stats
    PLAYER  = "player"   # Disponibilités only


class EventType(PyEnum):
    PRACC         = "pracc"
    OFFICIAL      = "official"
    MEETING       = "meeting"
    SCRIM_REQUEST = "scrim_request"


class AvailabilitySlot(PyEnum):
    MORNING   = "morning"    # 08h-12h
    AFTERNOON = "afternoon"  # 12h-18h
    EVENING   = "evening"    # 18h-23h
    ALL_DAY   = "all_day"


class MatchResult(PyEnum):
    WIN     = "win"
    LOSS    = "loss"
    DRAW    = "draw"
    PENDING = "pending"


# ── Models ────────────────────────────────────────────────────────────────────

class Team(Base):
    """
    Une équipe = un roster + un calendrier isolé.
    Plusieurs équipes peuvent coexister dans le même serveur Discord.
    """
    __tablename__ = "teams"

    id         = Column(Integer, primary_key=True)
    guild_id   = Column(BigInteger, nullable=False)   # Pas de unique → multi-équipes par guild
    name       = Column(String(100), nullable=False)
    tag        = Column(String(10),  nullable=True)   # Ex: NVI, G2, FNC...
    game       = Column(String(50),  default="Valorant")
    logo_url   = Column(String(500), nullable=True)
    region     = Column(String(10),  nullable=True)   # eu, na, ap...
    created_by = Column(BigInteger,  nullable=True)   # discord_id du fondateur
    is_active  = Column(Boolean,     default=True)
    created_at = Column(DateTime,    default=datetime.utcnow)

    # Deux équipes ne peuvent pas avoir le même nom dans le même serveur
    __table_args__ = (UniqueConstraint("guild_id", "name"),)

    players:  List["Player"]     = relationship("Player",     back_populates="team", cascade="all, delete-orphan")
    events:   List["Event"]      = relationship("Event",      back_populates="team", cascade="all, delete-orphan")
    members:  List["TeamMember"] = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Team(name={self.name}, guild_id={self.guild_id})>"


class TeamMember(Base):
    """
    Associe un utilisateur Discord à une équipe avec un rôle.
    Un utilisateur peut être membre de PLUSIEURS équipes du même serveur
    (ex: joueur en main team + coach en academy).
    """
    __tablename__ = "team_members"

    id         = Column(Integer, primary_key=True)
    team_id    = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    discord_id = Column(BigInteger, nullable=False)
    role       = Column(Enum(TeamRole), nullable=False, default=TeamRole.PLAYER)
    joined_at  = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("team_id", "discord_id"),)

    team: "Team" = relationship("Team", back_populates="members")


class GuildContext(Base):
    """
    Mémorise l'équipe "active" d'un utilisateur dans un serveur.
    Permet de résoudre "/pracc history" sans préciser l'équipe à chaque fois.
    Si un utilisateur n'est membre que d'une équipe → auto-resolve.
    Si plusieurs → on utilise ce contexte ou on demande.
    """
    __tablename__ = "guild_contexts"

    id            = Column(Integer,    primary_key=True)
    guild_id      = Column(BigInteger, nullable=False)
    discord_id    = Column(BigInteger, nullable=False)
    active_team_id = Column(Integer,   ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    updated_at    = Column(DateTime,   default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("guild_id", "discord_id"),)


class Player(Base):
    """Joueur inscrit dans le roster d'une équipe."""
    __tablename__ = "players"

    id         = Column(Integer,    primary_key=True)
    discord_id = Column(BigInteger, nullable=False)
    team_id    = Column(Integer,    ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    ign        = Column(String(100), nullable=False)  # Riot ID
    tag        = Column(String(10),  nullable=True)   # Riot Tag (#EUW)
    role       = Column(String(50),  nullable=True)   # IGL, Duelist, Sentinel...
    is_active  = Column(Boolean,     default=True)
    joined_at  = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("discord_id", "team_id"),)

    team:         "Team"                  = relationship("Team",              back_populates="players")
    availabilities: List["Availability"] = relationship("Availability",      back_populates="player", cascade="all, delete-orphan")
    performances: List["PlayerPerformance"] = relationship("PlayerPerformance", back_populates="player", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Player(ign={self.ign}, role={self.role})>"


class Availability(Base):
    __tablename__ = "availabilities"

    id         = Column(Integer,  primary_key=True)
    player_id  = Column(Integer,  ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    date       = Column(DateTime, nullable=False)
    slot       = Column(Enum(AvailabilitySlot), nullable=False)
    note       = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("player_id", "date", "slot"),)

    player: "Player" = relationship("Player", back_populates="availabilities")


class Event(Base):
    __tablename__ = "events"

    id           = Column(Integer,  primary_key=True)
    team_id      = Column(Integer,  ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    event_type   = Column(Enum(EventType),   nullable=False)
    title        = Column(String(200),       nullable=False)
    description  = Column(Text,              nullable=True)
    scheduled_at = Column(DateTime,          nullable=False)
    opponent     = Column(String(100),       nullable=True)
    map_played   = Column(String(50),        nullable=True)
    result       = Column(Enum(MatchResult), default=MatchResult.PENDING)
    rounds_won   = Column(Integer,           nullable=True)
    rounds_lost  = Column(Integer,           nullable=True)
    vod_url      = Column(String(500),       nullable=True)
    notes        = Column(Text,              nullable=True)
    pracc_id     = Column(String(100),       nullable=True)  # pracc.com ID si importé
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team:         "Team"                     = relationship("Team",             back_populates="events")
    performances: List["PlayerPerformance"] = relationship("PlayerPerformance", back_populates="event", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Event(title={self.title}, type={self.event_type})>"


class PlayerPerformance(Base):
    __tablename__ = "player_performances"

    id          = Column(Integer, primary_key=True)
    event_id    = Column(Integer, ForeignKey("events.id",  ondelete="CASCADE"), nullable=False)
    player_id   = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    agent       = Column(String(50),  nullable=True)
    kills       = Column(Integer,     nullable=True)
    deaths      = Column(Integer,     nullable=True)
    assists     = Column(Integer,     nullable=True)
    acs         = Column(Integer,     nullable=True)
    adr         = Column(Integer,     nullable=True)
    hs_percent  = Column(Integer,     nullable=True)
    first_bloods = Column(Integer,    nullable=True)
    notes       = Column(Text,        nullable=True)

    event:  "Event"  = relationship("Event",  back_populates="performances")
    player: "Player" = relationship("Player", back_populates="performances")


class MoodRating(PyEnum):
    VERY_BAD  = "1"
    BAD       = "2"
    NEUTRAL   = "3"
    GOOD      = "4"
    VERY_GOOD = "5"


class TeamMood(Base):
    """
    Mood hebdomadaire d'un joueur.
    Un joueur peut mettre à jour son mood à tout moment.
    On garde l'historique pour voir l'évolution dans le temps.
    """
    __tablename__ = "team_moods"

    id         = Column(Integer,    primary_key=True)
    player_id  = Column(Integer,    ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    team_id    = Column(Integer,    ForeignKey("teams.id",   ondelete="CASCADE"), nullable=False)
    rating     = Column(Enum(MoodRating), nullable=False)
    note       = Column(String(500), nullable=True)   # Justification libre
    week_start = Column(DateTime,   nullable=False)   # Lundi de la semaine
    created_at = Column(DateTime,   default=datetime.utcnow)
    updated_at = Column(DateTime,   default=datetime.utcnow, onupdate=datetime.utcnow)

    # Un seul mood par joueur par semaine (upsert)
    __table_args__ = (UniqueConstraint("player_id", "week_start"),)

    player: "Player" = relationship("Player")
    team:   "Team"   = relationship("Team")


class TeamChannels(Base):
    """
    Stocke les IDs des channels Discord créés automatiquement pour une équipe.
    Permet au bot de savoir où poster les updates (calendrier, roster, mood...).
    """
    __tablename__ = "team_channels"

    id                  = Column(Integer,    primary_key=True)
    team_id             = Column(Integer,    ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, unique=True)

    # Category IDs
    category_id         = Column(BigInteger, nullable=True)   # Catégorie principale
    staff_category_id   = Column(BigInteger, nullable=True)   # Catégorie staff (privée)

    # Public channels
    ch_announcements    = Column(BigInteger, nullable=True)   # 📢 annonces
    ch_calendar         = Column(BigInteger, nullable=True)   # 📅 calendrier
    ch_roster           = Column(BigInteger, nullable=True)   # 👥 roster
    ch_praccs           = Column(BigInteger, nullable=True)   # 🥊 praccs
    ch_general          = Column(BigInteger, nullable=True)   # 💬 général

    # Staff-only channels
    ch_staff_general    = Column(BigInteger, nullable=True)   # 🎙️ staff-général
    ch_mood             = Column(BigInteger, nullable=True)   # 💬 mood (staff)
    ch_logs             = Column(BigInteger, nullable=True)   # 📋 logs bot

    # Role IDs créés pour l'équipe
    role_staff_id       = Column(BigInteger, nullable=True)   # @TAG Staff
    role_player_id      = Column(BigInteger, nullable=True)   # @TAG Player

    created_at          = Column(DateTime,   default=datetime.utcnow)

    # Panel message IDs (persistent interactive embeds)
    panel_roster_msg    = Column(BigInteger, nullable=True)   # Message ID du panel #roster
    panel_calendar_msg  = Column(BigInteger, nullable=True)   # Message ID du panel #calendrier
    panel_praccs_msg    = Column(BigInteger, nullable=True)   # Message ID du panel #praccs
    panel_mood_msg      = Column(BigInteger, nullable=True)   # Message ID du panel #mood

    team: "Team" = relationship("Team")
