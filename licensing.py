"""Лицензии: бот работает только на серверах, где введён купленный ключ.

Владелец бота создаёт ключи командой /key create, покупатель вводит /activate КЛЮЧ.
Серверы, где бот уже стоял до появления лицензий, получают бессрочный доступ автоматически.
"""
import logging
import os
import secrets
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from db import conn
from emojis import EMOJI

DAY = 86400
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих символов 0/O, 1/I
SELLER_CONTACT = os.getenv("SELLER_CONTACT", "").strip() or "у владельца бота"
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")

# Команды, которые работают и на неактивированном сервере
OPEN_COMMANDS = {"activate", "license", "key"}

conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS license_keys (
        code TEXT PRIMARY KEY,
        days INTEGER NOT NULL,              -- 0 = навсегда
        note TEXT,
        created_at INTEGER NOT NULL,
        used_guild_id INTEGER,
        used_by INTEGER,
        used_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS licenses (
        guild_id INTEGER PRIMARY KEY,
        expires_at INTEGER,                 -- NULL = навсегда
        last_code TEXT,
        activated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS licensing_meta (
        name TEXT PRIMARY KEY,
        value TEXT
    );
    """
)
conn.commit()


def _free_guild_ids() -> set:
    ids = set()
    raw = ",".join(filter(None, [DEV_GUILD_ID, os.getenv("FREE_GUILD_IDS", "")]))
    for part in raw.replace(" ", "").split(","):
        if part.isdigit():
            ids.add(int(part))
    return ids


FREE_GUILDS = _free_guild_ids()
_cache: dict = {}  # guild_id -> expires_at (None = навсегда)


def _load_cache():
    _cache.clear()
    for row in conn.execute("SELECT guild_id, expires_at FROM licenses"):
        _cache[row["guild_id"]] = row["expires_at"]


_load_cache()


# ---------- проверки ----------
def is_licensed(guild_id: Optional[int]) -> bool:
    if guild_id is None or guild_id in FREE_GUILDS:
        return True
    if guild_id not in _cache:
        return False
    expires = _cache[guild_id]
    return expires is None or expires > time.time()


def guild_id_of(args) -> Optional[int]:
    """Находит сервер, к которому относится событие (сообщение, участник, payload…)."""
    for arg in args:
        if isinstance(arg, discord.Guild):
            return arg.id
        gid = getattr(arg, "guild_id", None)
        if isinstance(gid, int):
            return gid
        guild = getattr(arg, "guild", None)
        if isinstance(guild, discord.Guild):
            return guild.id
    return None


def not_activated_text() -> str:
    return (
        f"**Бот не активирован на этом сервере.**\n"
        f"Купите ключ {SELLER_CONTACT} и введите команду `/activate` с этим ключом "
        "(нужно право «Управлять сервером»)."
    )


# ---------- работа с ключами ----------
def new_code() -> str:
    parts = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "EAGLE-" + "-".join(parts)


def create_keys(days: int, count: int, note: Optional[str]) -> list:
    codes = []
    now = int(time.time())
    for _ in range(count):
        code = new_code()
        conn.execute(
            "INSERT INTO license_keys (code, days, note, created_at) VALUES (?, ?, ?, ?)",
            (code, days, note, now),
        )
        codes.append(code)
    conn.commit()
    return codes


def grant(guild_id: int, days: int, code: Optional[str] = None) -> Optional[int]:
    """Выдаёт/продлевает доступ. days=0 — навсегда. Возвращает новый срок (None = навсегда)."""
    now = int(time.time())
    row = conn.execute("SELECT expires_at FROM licenses WHERE guild_id = ?", (guild_id,)).fetchone()
    if days == 0 or (row and row["expires_at"] is None):
        expires = None
    else:
        base = row["expires_at"] if row and row["expires_at"] and row["expires_at"] > now else now
        expires = base + days * DAY
    conn.execute(
        "INSERT INTO licenses (guild_id, expires_at, last_code, activated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET expires_at = excluded.expires_at, last_code = excluded.last_code",
        (guild_id, expires, code, now),
    )
    conn.commit()
    _cache[guild_id] = expires
    return expires


def revoke(guild_id: int) -> bool:
    cur = conn.execute("DELETE FROM licenses WHERE guild_id = ?", (guild_id,))
    conn.commit()
    _cache.pop(guild_id, None)
    return cur.rowcount > 0


def redeem(code: str, guild_id: int, user_id: int):
    """Возвращает (ok, сообщение)."""
    code = code.strip().upper()
    row = conn.execute("SELECT * FROM license_keys WHERE code = ?", (code,)).fetchone()
    if not row:
        return False, "Такого ключа нет. Проверьте, что скопировали его полностью."
    if row["used_guild_id"]:
        return False, "Этот ключ уже использован."
    # помечаем ключ использованным атомарно, чтобы его нельзя было ввести дважды одновременно
    cur = conn.execute(
        "UPDATE license_keys SET used_guild_id = ?, used_by = ?, used_at = ? WHERE code = ? AND used_guild_id IS NULL",
        (guild_id, user_id, int(time.time()), code),
    )
    conn.commit()
    if cur.rowcount == 0:
        return False, "Этот ключ уже использован."
    expires = grant(guild_id, row["days"], code)
    return True, f"Бот активирован! Доступ {format_expiry(expires)}."


def format_expiry(expires: Optional[int]) -> str:
    if expires is None:
        return "**навсегда**"
    return f"до <t:{expires}:D> (<t:{expires}:R>)"


def days_label(days: int) -> str:
    return "навсегда" if days == 0 else f"{days} дн."


# ---------- команды ----------
async def _owner_only(interaction: discord.Interaction) -> bool:
    if await interaction.client.is_owner(interaction.user):
        return True
    await interaction.response.send_message("Эта команда только для владельца бота.", ephemeral=True)
    return False


KEY_GROUP = app_commands.Group(
    name="key",
    description="Ключи доступа к боту (только для владельца бота)",
    default_permissions=discord.Permissions(administrator=True),
)


@KEY_GROUP.command(name="create", description="Создать ключи доступа")
@app_commands.describe(days="Срок в днях (0 = навсегда)", count="Сколько ключей создать", note="Заметка: кому продан")
async def key_create(interaction: discord.Interaction, days: app_commands.Range[int, 0, 3650] = 30,
                     count: app_commands.Range[int, 1, 20] = 1, note: Optional[str] = None):
    if not await _owner_only(interaction):
        return
    codes = create_keys(days, count, note)
    lines = "\n".join(f"`{c}`" for c in codes)
    await interaction.response.send_message(
        f"Создано ключей: {len(codes)} (срок: {days_label(days)})\n{lines}\n\n"
        "Покупатель вводит на своём сервере: `/activate` и вставляет ключ.",
        ephemeral=True,
    )


@KEY_GROUP.command(name="list", description="Неиспользованные ключи")
async def key_list(interaction: discord.Interaction):
    if not await _owner_only(interaction):
        return
    rows = conn.execute(
        "SELECT code, days, note FROM license_keys WHERE used_guild_id IS NULL ORDER BY created_at DESC LIMIT 40"
    ).fetchall()
    if not rows:
        text = "Свободных ключей нет. Создайте: `/key create`."
    else:
        text = "**Свободные ключи:**\n" + "\n".join(
            f"`{r['code']}` — {days_label(r['days'])}" + (f" · {r['note']}" if r["note"] else "") for r in rows
        )
    await interaction.response.send_message(text[:1990], ephemeral=True)


@KEY_GROUP.command(name="servers", description="Все серверы бота и их доступ")
async def key_servers(interaction: discord.Interaction):
    if not await _owner_only(interaction):
        return
    lines = []
    for g in sorted(interaction.client.guilds, key=lambda g: g.name.lower()):
        if g.id in FREE_GUILDS:
            status = "бесплатно (ваш сервер)"
        elif g.id not in _cache:
            status = f"{EMOJI['no']} не активирован"
        else:
            status = (f"{EMOJI['ok']} " if is_licensed(g.id) else f"{EMOJI['wait']} истёк, был ") + format_expiry(_cache[g.id])
        lines.append(f"**{g.name}** (`{g.id}`): {status}")
    text = "\n".join(lines) or "Бот пока ни на одном сервере."
    await interaction.response.send_message(text[:1990], ephemeral=True)


@KEY_GROUP.command(name="grant", description="Выдать доступ серверу без ключа")
@app_commands.describe(server_id="ID сервера", days="Срок в днях (0 = навсегда)")
async def key_grant(interaction: discord.Interaction, server_id: str, days: app_commands.Range[int, 0, 3650] = 30):
    if not await _owner_only(interaction):
        return
    if not server_id.strip().isdigit():
        await interaction.response.send_message("ID сервера — это число.", ephemeral=True)
        return
    expires = grant(int(server_id), days, "выдано вручную")
    await interaction.response.send_message(f"Доступ серверу `{server_id}` {format_expiry(expires)}.", ephemeral=True)


@KEY_GROUP.command(name="revoke", description="Забрать доступ у сервера")
@app_commands.describe(server_id="ID сервера")
async def key_revoke(interaction: discord.Interaction, server_id: str):
    if not await _owner_only(interaction):
        return
    ok = server_id.strip().isdigit() and revoke(int(server_id))
    await interaction.response.send_message(
        f"Доступ у `{server_id}` забран." if ok else "У этого сервера не было доступа.", ephemeral=True
    )


@KEY_GROUP.command(name="delete", description="Удалить неиспользованный ключ")
async def key_delete(interaction: discord.Interaction, code: str):
    if not await _owner_only(interaction):
        return
    cur = conn.execute("DELETE FROM license_keys WHERE code = ? AND used_guild_id IS NULL", (code.strip().upper(),))
    conn.commit()
    await interaction.response.send_message(
        "Ключ удалён." if cur.rowcount else "Ключ не найден или уже использован.", ephemeral=True
    )


class Licensing(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="activate", description="Активировать бота на сервере ключом")
    @app_commands.describe(code="Ключ вида EAGLE-XXXX-XXXX-XXXX")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def activate(self, interaction: discord.Interaction, code: str):
        ok, text = redeem(code, interaction.guild_id, interaction.user.id)
        if ok:
            logging.info("Лицензия: сервер %s (%s) активирован ключом %s", interaction.guild.name,
                         interaction.guild_id, code.strip().upper())
            text += "\nСписок команд: `/all`."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="license", description="Срок доступа к боту на этом сервере")
    @app_commands.guild_only()
    async def license_status(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        if gid in FREE_GUILDS:
            text = "Доступ к боту: **навсегда**."
        elif gid in _cache and is_licensed(gid):
            text = f"Доступ к боту {format_expiry(_cache[gid])}."
        elif gid in _cache:
            text = "⌛ Срок доступа истёк.\n" + not_activated_text()
        else:
            text = not_activated_text()
        await interaction.response.send_message(text, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        # Один раз: серверы, где бот уже стоял до лицензий, получают бессрочный доступ.
        done = conn.execute("SELECT value FROM licensing_meta WHERE name = 'grandfathered'").fetchone()
        if done:
            return
        for g in self.bot.guilds:
            if g.id not in _cache:
                grant(g.id, 0, "был до лицензий")
        conn.execute("INSERT OR REPLACE INTO licensing_meta (name, value) VALUES ('grandfathered', '1')")
        conn.commit()
        logging.info("Лицензия: текущим серверам (%d) выдан бессрочный доступ", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        logging.info("Бота добавили на сервер %s (%s), активирован: %s", guild.name, guild.id, is_licensed(guild.id))
        if is_licensed(guild.id):
            return
        channel = guild.system_channel
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            channel = next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
        if channel:
            try:
                await channel.send("Спасибо, что добавили бота Eagle!\n" + not_activated_text())
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Licensing(bot))
    if not DEV_GUILD_ID:
        # нет «своего» сервера — команды /key видны админам везде, но работают только у владельца бота
        bot.tree.add_command(KEY_GROUP)
