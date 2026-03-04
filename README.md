# 🎮 Esports Team Manager Bot

> A public Discord bot for esports team management — built for coaches, managers and players.  
> Self-hosted once, usable by every organization that invites it.

---

## ✨ Features

| Command | What it does |
|---|---|
| `/team create` | Create a team — auto-generates channels, categories & roles |
| `/roster` | Add/remove players, view profiles |
| `/dispo` | Track weekly availability by time slot |
| `/cal` | Schedule praccs, officials, meetings |
| `/pracc` | Log scrim results, sync from pracc.com, per-map stats |
| `/mapstats` | Winrate, round ratio and streaks per map |
| `/mood` | Weekly team mood tracking with staff overview |
| `/stats` | Live Valorant stats via Henrik Dev API |

**Multi-team** — one server can run multiple teams (main + academy).  
**Bilingual** — FR/EN, auto-detected from the user's Discord language.

---

## 🚀 Deploying on Railway

### 1. Fork & push to GitHub

```bash
git clone https://github.com/your-username/esports-team-manager
cd esports-team-manager
git remote set-url origin https://github.com/your-username/your-fork.git
git push -u origin main
```

### 2. Create the Discord bot

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. **Bot** tab → **Reset Token** → copy it
3. Enable **Server Members Intent** + **Message Content Intent**
4. **OAuth2 → URL Generator** — scopes: `bot` + `applications.commands`  
   Permissions: `Manage Channels`, `Manage Roles`, `Send Messages`, `Embed Links`

### 3. Deploy on Railway

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → select your fork
2. Add a **PostgreSQL** service to the project — Railway auto-injects `DATABASE_URL` ✅
3. In your bot service → **Variables**, set:

| Variable | Required | Value |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Your bot token |
| `BOT_NAME` | — | `Team Manager` (shown in footers) |
| `HENRIK_API_KEY` | — | [Free key](https://docs.henrikdev.xyz) for `/stats` |
| `PRACC_EMAIL` | — | pracc.com account |
| `PRACC_PASSWORD` | — | pracc.com password |
| `PRACC_SYNC_ENABLED` | — | `true` to enable pracc.com sync |

4. Railway deploys automatically. Slash commands appear within ~1 hour (global sync).

> **Dev tip:** Add `GUILD_ID=your_server_id` for instant slash command sync during development.

---

## 🏗️ Architecture

```
esports-team-manager/
├── main.py                  # Entry point
├── config.py                # Env var config
├── Dockerfile               # Railway deployment
├── railway.toml
├── cogs/                    # Slash command modules
│   ├── team.py              # /team — creation, channels, roles
│   ├── roster.py            # /roster — player management
│   ├── availability.py      # /dispo — weekly availability
│   ├── calendar_cog.py      # /cal — event scheduling
│   ├── pracc.py             # /pracc — scrim tracking
│   ├── mapstats.py          # /mapstats — per-map stats
│   ├── mood.py              # /mood — team wellbeing
│   └── stats.py             # /stats — live API stats
├── database/
│   ├── models.py            # SQLAlchemy ORM
│   └── db.py                # Async session (PostgreSQL + SQLite)
├── utils/
│   ├── i18n.py              # FR/EN translation engine
│   ├── channels.py          # Auto Discord channel/role setup
│   ├── team_resolver.py     # Multi-team resolution
│   ├── embeds.py            # Shared embed builders
│   ├── scraper.py           # pracc.com scraper
│   └── valorant_api.py      # Henrik Dev API client
└── locales/
    ├── fr.json
    └── en.json
```

**Stack:** Python 3.12 · discord.py 2.x · SQLAlchemy 2.0 async · asyncpg · PostgreSQL

---

## 🔒 Auto-generated Discord structure

When a team is created with `/team create name:Vitality tag:VIT`:

```
〔🎮〕 Vitality               ← public category
  📢・announcements           ← read-only, bot posts here
  📅・calendar                ← auto-updated event schedule
  👥・roster                  ← auto-updated player list
  🥊・praccs                  ← scrim results and stats
  💬・general                 ← open team discussion

〔🔒〕 Vitality Staff         ← private (invisible to players)
  🎙️・staff-general
  💬・mood-overview
  📋・logs-bot

@VIT Staff   ← red, auto-assigned to coaches/managers
@VIT Player  ← blue, auto-assigned on /roster add
```

---

## 📄 License

MIT
