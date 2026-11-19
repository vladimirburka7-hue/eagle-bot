"""Стартовая версия бота: приветствие, защита от спама/рейдов, веселые команды.

Все настройки по серверам хранятся в SQLite (bot.db), поэтому бота можно
позже сделать публичным без переписывания кода.
"""
import logging
import os
import random
import re
import sys
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

REQUIRED_FILES = (
    "db.py", "utils.py", "content.py", "emojis.py", "tickets.py", "news.py", "profiles.py", "activity.py",
    "reports.py", "stacks.py", "memo.py", "market.py", "guide.py", "voice.py", "automod.py", "badwords.py", "licensing.py", "shield.py",
    "modes.py", "shop.py", "gate.py", "banner.py",
)
_missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), f))]
if _missing:
    raise SystemExit(
        "\nВ папке бота не хватает файлов: " + ", ".join(_missing) + "\n"
        "Положите в папку ВСЕ файлы из последнего архива (с заменой) и запустите снова."
    )

# Только IPv4: если IPv6-маршрут до Discord не работает, соединение зависает и падает по таймауту.
# Отключить можно строкой FORCE_IPV4=0 в файле .env.
import socket

if os.getenv("FORCE_IPV4", "1") != "0":
    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4_getaddrinfo

# единая настройка журнала: без дублей строк, и наши сообщения (версия, список команд) тоже видны
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

import db
import shield
from content import build_rules_payload, post_components
from emojis import EMOJI, load_app_emojis

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # необязательно: с этого сервера убираются старые дубли команд
USE_MESSAGE_CONTENT = os.getenv("USE_MESSAGE_CONTENT", "0") == "1"
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS", "0") == "1"
PROXY_URL = os.getenv("PROXY_URL") or None  # например http://127.0.0.1:8080, если Discord доступен только через прокси

MIN_ACCOUNT_AGE_DAYS = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", "30"))  # устарело: теперь /mod shield setup (по умолчанию 30)
RAID_JOINS = int(os.getenv("RAID_JOINS", "5"))
RAID_WINDOW = int(os.getenv("RAID_WINDOW_SECONDS", "30"))
SPAM_MESSAGES = int(os.getenv("SPAM_MESSAGES", "6"))
SPAM_WINDOW = int(os.getenv("SPAM_WINDOW_SECONDS", "5"))
SPAM_TIMEOUT_MIN = int(os.getenv("SPAM_TIMEOUT_MINUTES", "10"))

INVITE_RE = re.compile(r"(discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", re.I)

# ---------- Бот ----------
intents = discord.Intents.default()
intents.members = True  # нужен Server Members Intent в Developer Portal
intents.message_content = USE_MESSAGE_CONTENT  # только если включено в портале
# События, которые доходят до бота даже на неактивированном сервере
ALWAYS_EVENTS = {"guild_join", "guild_remove", "guild_available", "guild_unavailable", "guild_update",
                 "interaction", "app_command_completion"}


class LicensedTree(app_commands.CommandTree):
    """Слэш-команды работают только на серверах с купленным ключом (кроме /activate, /license, /key)."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        import licensing
        if interaction.guild_id is None or licensing.is_licensed(interaction.guild_id):
            return True
        cmd = interaction.command
        root = getattr(cmd, "root_parent", None) or cmd
        if root is not None and root.name in licensing.OPEN_COMMANDS:
            return True
        if interaction.type is discord.InteractionType.application_command:
            await interaction.response.send_message(licensing.not_activated_text(), ephemeral=True)
        return False


class LicensedBot(commands.Bot):
    """Не обрабатывает события (сообщения, входы, войсы…) с неактивированных серверов."""

    def dispatch(self, event_name, /, *args, **kwargs):
        if event_name not in ALWAYS_EVENTS and "licensing" in sys.modules:
            licensing = sys.modules["licensing"]
            gid = licensing.guild_id_of(args)
            if gid is not None and not licensing.is_licensed(gid):
                return
        super().dispatch(event_name, *args, **kwargs)


bot = LicensedBot(command_prefix=commands.when_mentioned, intents=intents, proxy=PROXY_URL, tree_cls=LicensedTree)

recent_joins = defaultdict(deque)
recent_messages = defaultdict(lambda: deque(maxlen=SPAM_MESSAGES))


BUILD = "2026-11-07-style"  # версия сборки: по ней видно, какие файлы запущены


def commands_overview() -> str:
    """Все зарегистрированные команды, группы с подкомандами: «market (post, my, search, guide)»."""
    parts = []
    for cmd in bot.tree.get_commands():
        if isinstance(cmd, app_commands.Group):
            parts.append(f"{cmd.name} ({', '.join(sorted(c.name for c in cmd.commands))})")
        else:
            parts.append(cmd.name)
    return "; ".join(sorted(parts))


BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
BACKUP_KEEP = 7  # сколько последних копий хранить


def make_backup() -> str:
    """Сохраняет копию базы в папку backups и удаляет самые старые."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"bot-{time.strftime('%Y-%m-%d_%H%M')}.db")
    db.backup_to(path)
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("bot-") and f.endswith(".db"))
    for old in files[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return path


@tasks.loop(hours=24)
async def daily_backup():
    try:
        logging.info("Резервная копия базы: %s", make_backup())
    except Exception:
        logging.exception("Не удалось сделать резервную копию базы")


EXTENSIONS = ("tickets", "news", "profiles", "activity", "reports", "stacks", "memo", "market", "guide", "voice", "automod", "licensing", "shield",
              "modes", "shop", "gate", "banner")


# Настройки собраны в две команды (у Discord лимит на размер одной команды):
#   /setting <раздел> — apps, stack, report, market, voice, verify, partners, profile, bot
#   /mod <раздел>     — automod, shield (рядом с mute, unmute и т.д.)
# Памятка остаётся отдельно: /info и /info_setting.
SETTING_GROUPS = {
    "app_setting": ("setting", "apps"), "stack_setting": ("setting", "stack"), "report_setting": ("setting", "report"),
    "market_setting": ("setting", "market"), "voice_setting": ("setting", "voice"), "verify_setting": ("setting", "verify"),
    "partners_setting": ("setting", "partners"), "profile_setting": ("setting", "profile"), "bot_setting": ("setting", "bot"),
    "automod_setting": ("mod", "automod"), "shield": ("mod", "shield"),
}


def merge_settings():
    roots = {"setting": app_commands.Group(name="setting", description="Настройки бота (для персонала)", guild_only=True)}
    mod = bot.tree.get_command("mod")
    if isinstance(mod, app_commands.Group):
        roots["mod"] = mod
    for old, (root_name, new) in SETTING_GROUPS.items():
        group = bot.tree.get_command(old)
        root = roots.get(root_name)
        if not isinstance(group, app_commands.Group) or root is None:
            continue
        bot.tree.remove_command(old)
        group.name = new
        for attr, value in (("guild_only", False), ("default_permissions", None)):
            try:  # у вложенных групп Discord эти поля не принимает
                setattr(group, attr, value)
            except AttributeError:
                pass
        root.add_command(group)
    bot.tree.add_command(roots["setting"])


async def sync_all_commands() -> list[str]:
    """Синхронизирует глобальные команды и команды DEV_GUILD_ID."""
    synced = await bot.tree.sync()
    if DEV_GUILD_ID:
        import licensing
        guild = discord.Object(id=int(DEV_GUILD_ID))
        bot.tree.clear_commands(guild=guild)
        bot.tree.add_command(licensing.KEY_GROUP, guild=guild)
        await bot.tree.sync(guild=guild)
    return sorted(c.name for c in synced)


@bot.event
async def setup_hook():
    await load_app_emojis(bot)
    daily_backup.start()
    for name in EXTENSIONS:
        try:
            await bot.load_extension(name)
        except (commands.ExtensionNotFound, commands.ExtensionFailed) as e:
            cause = getattr(e, "original", e)
            if isinstance(e, commands.ExtensionNotFound) or isinstance(cause, ImportError):
                raise SystemExit(
                    f"\nМодуль «{name}» не загрузился: {cause}\n"
                    "Скорее всего, файлы бота разных версий (часть старые, часть новые).\n"
                    "Замените ВСЕ файлы бота из последнего архива и запустите снова."
                )
            raise
    merge_settings()
    logging.info("Сборка %s. Команды бота: %s", BUILD, commands_overview())

    if SYNC_COMMANDS:
        try:
            names = await sync_all_commands()
            logging.info("Команды синхронизированы (все серверы): %d шт.: %s", len(names), ", ".join(names))
            if "all" not in names:
                logging.warning("Команды /all нет в списке: проверьте, что файл guide.py лежит в папке бота")
        except discord.HTTPException as e:
            logging.error("Discord не принял список команд при запуске: %s", e)
    else:
        logging.info("Автосинхронизация команд отключена (SYNC_COMMANDS=0). Для обновления используйте /setting bot sync")


@bot.event
async def on_ready():
    logging.info("Бот в сети: %s (серверов: %d)", bot.user, len(bot.guilds))


async def send_log(guild: discord.Guild, text: str):
