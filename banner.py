"""Баннер сверху сообщений бота убран.

При запуске бот один раз убирает старый баннер из своих прошлых сообщений
(если он был включён) и удаляет файл баннера. Команды /banner больше нет.
"""
import logging
import os

import discord
from discord.ext import commands

from content import BANNER_DIR, banner_file, rebanner_message

SCAN_LIMIT = 300  # сколько последних сообщений смотреть в каждом канале


class Banner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.done = False

    async def strip_guild(self, guild: discord.Guild) -> int:
        changed = 0
        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if not (perms.view_channel and perms.read_message_history):
                continue
            try:
                async for msg in ch.history(limit=SCAN_LIMIT):
                    if msg.author.id != self.bot.user.id or not (msg.flags.value & 32768):
                        continue
                    try:
                        if await rebanner_message(self.bot, ch.id, msg.id, guild.id, add=False):
                            changed += 1
                    except discord.HTTPException as e:
                        logging.warning("Баннер: не удалось изменить сообщение %s в #%s: %s", msg.id, ch.name, e)
            except discord.HTTPException:
                continue
        return changed

    @commands.Cog.listener()
    async def on_ready(self):
        if self.done or not os.path.isdir(BANNER_DIR):
            return
        self.done = True
        for guild in self.bot.guilds:
            path = banner_file(guild.id)
            if not path:
                continue
            changed = await self.strip_guild(guild)
            try:
                os.remove(path)
            except OSError:
                pass
            logging.info("Баннер убран из %d сообщений на сервере %s", changed, guild.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Banner(bot))
