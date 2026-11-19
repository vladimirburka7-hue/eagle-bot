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
    try:
        # Команды всегда регистрируются глобально — так они есть на ВСЕХ серверах.
        synced = await bot.tree.sync()
        where = "все серверы"
        if DEV_GUILD_ID:
            # Раньше команды регистрировались только для этого сервера.
            # Убираем те старые копии, иначе там каждая команда будет показана дважды.
            # Здесь же — команды владельца /key (их видно только на вашем сервере).
            import licensing
            guild = discord.Object(id=int(DEV_GUILD_ID))
            bot.tree.clear_commands(guild=guild)
            bot.tree.add_command(licensing.KEY_GROUP, guild=guild)
            await bot.tree.sync(guild=guild)
    except discord.HTTPException as e:
        raise SystemExit(
            f"\nDiscord не принял список команд: {e}\n"
            "Пришлите этот текст целиком, я исправлю."
        )
    logging.info("Сборка %s. Команды бота: %s", BUILD, commands_overview())
    names = sorted(c.name for c in synced)
    logging.info("Команды синхронизированы (%s): %d шт.: %s", where, len(names), ", ".join(names))
    if "all" not in names:
        logging.warning("Команды /all нет в списке: проверьте, что файл guide.py лежит в папке бота")


@bot.event
async def on_ready():
    logging.info("Бот в сети: %s (серверов: %d)", bot.user, len(bot.guilds))


async def send_log(guild: discord.Guild, text: str):
    channel_id = db.get_setting(guild.id, "log_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


# ---------- Приветствие, рейды ----------
@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    guild = member.guild

    # Защита от рейдов: много входов за короткое время
    now = time.monotonic()
    joins = recent_joins[guild.id]
    joins.append(now)
    while joins and now - joins[0] > RAID_WINDOW:
        joins.popleft()
    if len(joins) >= RAID_JOINS:
        joins.clear()
        await send_log(
            guild,
            f"Возможный рейд: {RAID_JOINS}+ входов за {RAID_WINDOW} сек. Проверьте новичков.",
        )

    # Слишком новый аккаунт (моложе 30 дней): кик или уведомление персоналу — см. shield.py
    if await shield.handle_join(member):
        return

    previous = db.register_join(guild.id, member.id)
    db.set_first_join(guild.id, member.id, int(time.time()))

    channel_id = db.get_setting(guild.id, "welcome_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if not channel:
        return

    ts = int(member.created_at.timestamp())
    if previous == 0:
        title = f"{EMOJI['title']} **{member.display_name}**, добро пожаловать!"
    else:
        title = f"{EMOJI['heart']} **{member.display_name}**, с возвращением!"
    memo = db.get_memo(guild.id) or {}
    start = f" Начни с <#{memo['channel_id']}>." if memo.get("channel_id") else ""
    embed = discord.Embed(
        description=f"{title}\nТы у нас {guild.member_count}-й.{start}\n-# в Discord с <t:{ts}:D>",
        color=0x2B2D31,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logging.warning("Не удалось отправить приветствие на сервере %s", guild.id)


# ---------- Защита от спама ----------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    # Новичкам нельзя файлы/ссылки/стикеры первые часы — см. shield.py
    if await shield.handle_message(message):
        return
    member = message.author
    perms = message.channel.permissions_for(member)
    if perms.manage_messages or perms.administrator:
        return

    # Слишком частые сообщения -> таймаут
    now = time.monotonic()
    history = recent_messages[(message.guild.id, member.id)]
    history.append(now)
    if len(history) == SPAM_MESSAGES and now - history[0] <= SPAM_WINDOW:
        history.clear()
        try:
            await member.timeout(
                timedelta(minutes=SPAM_TIMEOUT_MIN), reason="Спам"
            )
            await message.channel.send(
                f"{member.mention}, не спамь. Мут на {SPAM_TIMEOUT_MIN} мин.",
                delete_after=10,
            )
            await send_log(message.guild, f"Спам: таймаут {member.mention}")
        except discord.Forbidden:
            await send_log(
                message.guild,
                f"Нет прав на таймаут: роль бота выше ролей участников + «Модерировать участников».",
            )
        return

    # Ссылки-приглашения (нужен Message Content Intent)
    if USE_MESSAGE_CONTENT and INVITE_RE.search(message.content):
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        await message.channel.send(
            f"{member.mention}, приглашения запрещены.", delete_after=10
        )
        await send_log(message.guild, f"Удалено приглашение от {member.mention}")


# ---------- Команды ----------
@bot.tree.command(name="ping", description="Проверить, что бот жив")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"{round(bot.latency * 1000)} мс", ephemeral=True)


bot_setting = app_commands.Group(name="bot_setting", description="Настройки бота", guild_only=True)
bot.tree.add_command(bot_setting)


@bot_setting.command(name="welcome", description="Выбрать канал для приветствий")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel):
    db.set_setting(interaction.guild_id, "welcome_channel", channel.id)
    await interaction.response.send_message(
        f"Приветствия: {channel.mention}", ephemeral=True
    )


@bot_setting.command(name="logs", description="Выбрать канал для логов защиты")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_logs(interaction: discord.Interaction, channel: discord.TextChannel):
    db.set_setting(interaction.guild_id, "log_channel", channel.id)
    await interaction.response.send_message(
        f"Логи: {channel.mention}", ephemeral=True
    )


@bot_setting.command(name="check", description="Версия бота и список зарегистрированных команд")
async def check(interaction: discord.Interaction):
    perms = interaction.user.guild_permissions
    if not (perms.administrator or perms.manage_guild):
        await interaction.response.send_message("Только для администраторов.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"**Сборка:** {BUILD}\n**Команды:** {commands_overview()}"[:1900], ephemeral=True
    )


@bot_setting.command(name="backup", description="Прислать копию базы данных бота (только администраторам)")
async def backup(interaction: discord.Interaction):
    perms = interaction.user.guild_permissions
    if not (perms.administrator or perms.manage_guild):
        await interaction.response.send_message("Только для администраторов: в базе данные участников.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        path = make_backup()
    except Exception as e:
        logging.exception("Не удалось сделать копию базы")
        await interaction.followup.send(f"Не удалось сделать копию: {type(e).__name__}", ephemeral=True)
        return
    await interaction.followup.send(
        "Копия базы. Храните её в безопасном месте, в ней данные участников.",
        file=discord.File(path), ephemeral=True,
    )


@bot.tree.command(name="rules", description="Отправить сообщение с правилами сервера")
@app_commands.describe(channel="Канал для правил (по умолчанию текущий)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def rules(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    target = channel or interaction.channel
    await interaction.response.defer(ephemeral=True)
    try:
        await post_components(bot, target, build_rules_payload())
    except discord.HTTPException as e:
        await interaction.followup.send(f"Не удалось: {e}", ephemeral=True)
        return
    await interaction.followup.send(f"Правила в {target.mention}", ephemeral=True)


@bot.tree.command(name="roll", description="Бросить кубик")
@app_commands.describe(sides="Сколько граней (по умолчанию 6)")
async def roll(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 1000] = 6):
    await interaction.response.send_message(f"**{random.randint(1, sides)}** из {sides}")


@bot.tree.command(name="ball", description="Шар предсказаний")
@app_commands.describe(question="Ваш вопрос")
async def ball(interaction: discord.Interaction, question: str):
    answers = [
        "Да", "Нет", "Скорее да", "Скорее нет", "Спроси позже",
        "Без сомнений", "Даже не думай", "Всё возможно",
    ]
    await interaction.response.send_message(f"{question}\n**{random.choice(answers)}**")


@bot.tree.command(name="choose", description="Выбрать случайный вариант")
@app_commands.describe(options="Варианты через запятую")
async def choose(interaction: discord.Interaction, options: str):
    items = [x.strip() for x in options.split(",") if x.strip()]
    if len(items) < 2:
        await interaction.response.send_message("Напишите минимум два варианта через запятую.", ephemeral=True)
        return
    await interaction.response.send_message(f"**{random.choice(items)}**")


@bot.tree.command(name="serverinfo", description="Информация о сервере")
@app_commands.guild_only()
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    ts = int(guild.created_at.timestamp())
    await interaction.response.send_message(
        f"## {guild.name}\nУчастников: {guild.member_count}, бустов: {guild.premium_subscription_count}\n"
        f"-# создан <t:{ts}:D>", ephemeral=True
    )


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        text = "У тебя нет прав на это."
    else:
        logging.exception("Ошибка команды", exc_info=error)
        text = "Что-то пошло не так, попробуй ещё раз."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не найден DISCORD_TOKEN. Создайте файл .env (см. .env.example).")
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Включите Server Members Intent (и Message Content Intent, если "
            "USE_MESSAGE_CONTENT=1) в Developer Portal → Bot → Privileged Gateway Intents."
        )
    except (aiohttp.ClientError, OSError) as e:
        print(
            "\nНет связи с Discord (discord.com:443): " + str(e) + "\n"
            "Это проблема сети, а не бота. Проверьте, что программа обхода блокировок запущена и "
            "действует на ВСЕ программы (не только на Discord и браузер), или включите VPN на весь "
            "компьютер, или укажите прокси в PROXY_URL в файле .env.\n"
            "Для диагностики запустите netcheck.bat из папки бота и пришлите, что он покажет."
        )
        sys.exit(1)
