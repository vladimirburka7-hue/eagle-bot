"""Команда /news: заголовок + текст + медиа, оформление в стиле сервера."""
import logging
import os
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from content import build_news_payload, post_components


class NewsModal(discord.ui.Modal, title="Новая новость"):
    heading = discord.ui.TextInput(label="Заголовок", max_length=100)
    body = discord.ui.TextInput(
        label="Текст",
        style=discord.TextStyle.paragraph,
        max_length=3500,
        placeholder="Можно писать в несколько строк",
    )

    def __init__(self, bot, channel, media: Optional[discord.Attachment], plain: bool):
        super().__init__(timeout=900)
        self.bot = bot
        self.channel = channel
        self.media = media
        self.plain = plain

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        upload = None
        media_info = None
        if self.media:
            try:
                data = await self.media.read()
            except discord.HTTPException:
                await interaction.followup.send("Не удалось скачать вложение.", ephemeral=True)
                return
            ext = os.path.splitext(self.media.filename)[1]
            ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:10]
            is_image = (self.media.content_type or "").startswith("image/")
            filename = ("news_image" if is_image else "news_file") + (ext or ".bin")
            upload = (data, filename)
            media_info = ("image" if is_image else "file", filename)

        payload = build_news_payload(
            self.heading.value, self.body.value, media_info, self.plain
        )
        try:
            await post_components(self.bot, self.channel, payload, upload)
        except discord.Forbidden:
            await interaction.followup.send(
                f"У бота нет прав писать в {self.channel.mention}.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            logging.exception("Не удалось отправить новость")
            await interaction.followup.send(f"Не удалось отправить новость: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"Новость отправлена в {self.channel.mention}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы новости", exc_info=error)
        text = "Что-то пошло не так, попробуйте ещё раз."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class News(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="news", description="Опубликовать новость в стиле сервера")
    @app_commands.describe(
        media="Картинка, гиф или файл (необязательно)",
        channel="Канал для новости (по умолчанию текущий)",
        plain="Текст без рамки (если нужно форматирование: жирный, ссылки)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def news(
        self,
        interaction: discord.Interaction,
        media: Optional[discord.Attachment] = None,
        channel: Optional[discord.TextChannel] = None,
        plain: bool = False,
    ):
        target = channel or interaction.channel
        if media and media.size > interaction.guild.filesize_limit:
            await interaction.response.send_message(
                "Файл слишком большой для этого сервера.", ephemeral=True
            )
            return
        await interaction.response.send_modal(NewsModal(self.bot, target, media, plain))


async def setup(bot: commands.Bot):
    await bot.add_cog(News(bot))
