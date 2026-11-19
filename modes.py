# modes.py — режимы бота Eagle для разных серверов
# На каждом сервере свой режим: "clan" (как сейчас) или "shop" (магазин / заказы).
# Команда /mode скрыта: её видят только администраторы, а сработает она только у владельца бота.
# Ответы видишь только ты, в логи ничего не пишется, в /all команды нет.

import json
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# ===== НАСТРОЙКИ =====
SAVE_TO_FILE = True          # True: режим запоминается в modes.json рядом с ботом (переживает перезапуск)
                             # False: ничего никуда не пишется, режим живёт до перезапуска бота
DEFAULT_SHOP_GUILDS = set()  # если SAVE_TO_FILE = False, впиши сюда ID магазинного сервера: {123456789012345678}

# Какие слэш-команды относятся к магазину. Всё остальное считается командами клана.
SHOP_COMMANDS = {"памятка", "новость", "отзыв", "заказы", "настройка_магазина", "правила_магазина", "инструкция_отзывов"}
# Команды, которые работают в любом режиме (общие: модерация, защита, лицензия, настройки)
ANY_MODE = {"mode", "all", "ping", "serverinfo", "roll", "ball", "choose", "rules",
            "bot_setting", "license", "activate", "key", "shield", "mod", "warn", "automod_setting", "banner"}

MODES = {"clan": "Клан", "shop": "Магазин"}
MODES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modes.json")


def _load() -> dict:
    if SAVE_TO_FILE and os.path.exists(MODES_FILE):
        try:
            with open(MODES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {str(g): "shop" for g in DEFAULT_SHOP_GUILDS}


_modes: dict = _load()


def _save() -> None:
    if not SAVE_TO_FILE:
        return
    with open(MODES_FILE, "w", encoding="utf-8") as f:
        json.dump(_modes, f, ensure_ascii=False, indent=2)


def get_mode(guild_id: Optional[int]) -> str:
    """Режим сервера. Используй в обработчиках событий: if get_mode(member.guild.id) != "clan": return"""
    if guild_id is None:
        return "clan"
    return _modes.get(str(guild_id), "clan")


def is_clan(guild_id: Optional[int]) -> bool:
    return get_mode(guild_id) == "clan"


def is_shop(guild_id: Optional[int]) -> bool:
    return get_mode(guild_id) == "shop"


class Modes(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._old_check = bot.tree.interaction_check
        bot.tree.interaction_check = self._gate  # фильтр для всех слэш-команд

    def cog_unload(self):
        self.bot.tree.interaction_check = self._old_check

    async def _gate(self, interaction: discord.Interaction) -> bool:
        if not await self._old_check(interaction):
            return False
        cmd = interaction.command
        if cmd is None or interaction.guild_id is None:
            return True  # кнопки, меню, личка — не трогаем
        root = cmd.root_parent.name if getattr(cmd, "root_parent", None) else cmd.name
        if root in ANY_MODE:
            return True
        mode = get_mode(interaction.guild_id)
        allowed = (root in SHOP_COMMANDS) if mode == "shop" else (root not in SHOP_COMMANDS)
        if not allowed:
            await interaction.response.send_message("Эта команда не работает на этом сервере.", ephemeral=True)
        return allowed

    @app_commands.command(name="mode", description="Режим бота на этом сервере")
    @app_commands.describe(mode="Новый режим. Пусто — показать текущий")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Клан", value="clan"),
        app_commands.Choice(name="Магазин", value="shop"),
    ])
    @app_commands.default_permissions(administrator=True)  # обычные участники команду не видят
    @app_commands.guild_only()
    async def mode(self, interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None):
        if not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        gid = str(interaction.guild_id)
        if mode is None:
            cur = get_mode(interaction.guild_id)
            await interaction.response.send_message(f"Сейчас режим: **{MODES[cur]}**", ephemeral=True)
            return
        _modes[gid] = mode.value
        _save()
        await interaction.response.send_message(f"Готово. Режим на этом сервере: **{MODES[mode.value]}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Modes(bot))
