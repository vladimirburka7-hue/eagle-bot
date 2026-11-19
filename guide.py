"""Команда /all: список основных команд бота, сгруппированный по тому, кому они доступны."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from content import build_button_payload, respond_components
from utils import can_manage, captain_slot_of

try:
    from modes import is_shop
except ImportError:
    def is_shop(_gid):
        return False

# (команда, коротко что делает)
EVERYONE = [
    ("/info", "памятка сервера"),
    ("/profile", "твой профиль или чужой"),
    ("/activity", "активность, /top — самые активные"),
    ("/stack", "вступить в стак или выйти"),
    ("/market", "рынок услуг"),
    ("/voice", "свой войс"),
    ("/games", "выбрать игры"),
    ("/partners", "партнёры сервера"),
    ("/roll", "кубик, /ball — шар, /choose — выбор"),
]
CAPTAINS = [
    ("/report", "недельный отчёт"),
    ("/squad", "активность отряда"),
]
STAFF = [
    ("/setting", "настройки бота"),
    ("/info_setting", "настройки памятки"),
    ("/mod", "муты, автомодерация и защита"),
    ("/warn", "нарушения, /reward — награды"),
    ("/news", "новость, /rules — правила"),
]

# Магазинный режим (сервер «Команда Westeria»)
SHOP_EVERYONE = [
    ("/отзыв", "оставить отзыв"),
    ("Памятка → «Оставить заявку»", "заказать бота"),
]
SHOP_STAFF = [
    ("/памятка", "памятка с заказом"),
    ("/новость", "новость"),
    ("/заказы", "открытые заявки"),
    ("/настройка_магазина", "каналы и роли"),
    ("/mod", "модерация, /setting — настройки"),
]


def head(emoji, text):
    from emojis import EMOJI
    return f"{EMOJI[emoji]} {text}"


def block(items):
    return "\n".join(f"`{cmd}` {desc}" for cmd, desc in items)


class Guide(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="all", description="Список основных команд бота")
    @app_commands.guild_only()
    async def all_commands(self, interaction: discord.Interaction):
        member = interaction.user
        staff = can_manage(member)
        captain = captain_slot_of(interaction.guild, member)[0]
        if is_shop(interaction.guild_id):
            sections = [(head("title", "Для всех"), block(SHOP_EVERYONE))]
            if staff:
                sections.append((head("mod", "Персоналу"), block(SHOP_STAFF)))
            payload = build_button_payload(title="Команды", emoji="rules", sections=[], extra_blocks=sections)
            try:
                await respond_components(interaction.client, interaction, payload)
            except discord.HTTPException:
                await interaction.response.send_message("\n\n".join(f"**{h}**\n{b}" for h, b in sections)[:1990], ephemeral=True)
            return
        sections = [(head("title", "Для всех"), block(EVERYONE))]
        if captain or staff:
            sections.append((head("squad", "Капитанам"), block(CAPTAINS)))
        if staff:
            sections.append((head("mod", "Персоналу"), block(STAFF)))
        payload = build_button_payload(title="Команды", emoji="rules", sections=[], extra_blocks=sections)
        try:
            await respond_components(interaction.client, interaction, payload)
            return
        except discord.HTTPException:
            logging.exception("Не удалось ответить сообщением Components V2, отправляю обычное")
        text = "\n\n".join(f"**{head}**\n{body}" for head, body in sections)
        if interaction.response.is_done():
            await interaction.followup.send(text[:1990], ephemeral=True)
        else:
            await interaction.response.send_message(text[:1990], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Guide(bot))
