"""
Esports Team Manager Bot — Entry point.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands

from config import config
from database import init_db, close_db
from cogs import COGS


def setup_logging() -> None:
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # File logging only when not on Railway (Railway captures stdout natively)
    if not os.getenv("RAILWAY_ENVIRONMENT"):
        handlers.append(logging.FileHandler("bot.log", encoding="utf-8"))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


logger = logging.getLogger("bot")


class TeamManagerBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            description=config.bot_description,
        )

    async def setup_hook(self) -> None:
        # Only create local data dir for SQLite (not needed on Railway)
        if not config.is_postgres:
            try:
                os.makedirs("data", exist_ok=True)
            except PermissionError:
                pass  # Running in Docker as non-root — SQLite not available anyway

        await init_db(config.database_url)
        logger.info(f"Starting {config.bot_name}...")

        failed = []
        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info(f"  ✓ Loaded: {cog}")
            except Exception as e:
                logger.error(f"  ✗ Failed: {cog} — {e}", exc_info=True)
                failed.append(cog)

        if failed:
            logger.warning(f"{len(failed)} cog(s) failed to load: {failed}")

        # Always sync globally first
        synced = await self.tree.sync()
        logger.info(f"Slash commands synced globally ({len(synced)} commands)")

        # If GUILD_ID is set, also sync instantly to that guild (dev mode)
        if config.guild_id:
            try:
                guild = discord.Object(id=config.guild_id)
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                logger.info(f"Also synced to guild {config.guild_id} ({len(guild_synced)} commands)")
            except discord.Forbidden:
                logger.warning(f"Cannot sync to guild {config.guild_id} — bot not in that server or missing access. Skipping guild sync.")

    async def close(self) -> None:
        await close_db()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        guild_count = len(self.guilds)
        logger.info(f"Ready: {self.user} (ID: {self.user.id}) — {guild_count} server(s)")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"/team create · {guild_count} servers",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info(f"Joined server: {guild.name} (ID: {guild.id}, members: {guild.member_count})")
        # Update status with new server count
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"/team create · {len(self.guilds)} servers",
            )
        )
        # Send welcome message
        channel = guild.system_channel or next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
            None,
        )
        if channel:
            embed = discord.Embed(
                title=f"👋  {config.bot_name}",
                description=(
                    "**Esports team management, right in Discord.**\n\n"
                    "**Get started:**\n"
                    "> `/team create` — Create your team (auto-setups channels & roles)\n"
                    "> `/roster add` — Add players to your roster\n"
                    "> `/team invite` — Invite your coach or manager\n"
                    "> `/cal add` — Schedule your first pracc\n\n"
                    "Type `/` to explore all commands."
                ),
                color=0xFF4655,
            )
            embed.set_footer(text=config.bot_name)
            await channel.send(embed=embed)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info(f"Left server: {guild.name} (ID: {guild.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"/team create · {len(self.guilds)} servers",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        logger.error(
            f"Command error [{interaction.command}] by {interaction.user} "
            f"in {interaction.guild}: {error}",
            exc_info=True,
        )
        embed = discord.Embed(
            title="❌  Something went wrong",
            description=(
                "An unexpected error occurred. Please try again.\n"
                f"```{str(error)[:300]}```"
            ),
            color=0xBD3944,
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass


async def main() -> None:
    setup_logging()
    config.validate()
    bot = TeamManagerBot()
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down gracefully.")
