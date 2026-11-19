"""Защита от «сноса» сервера массовыми жалобами.

Схема атаки: твинки заходят, кидают запрещённый контент и сразу жалуются на сервер.
Защита:
  1) аккаунты моложе N дней (по умолчанию 30) не пускаются — бот их кикает;
  2) новичкам первые часы на сервере нельзя кидать файлы, ссылки, стикеры, эмбеды;
  3) обо всём подозрительном сразу приходит сообщение в чат персонала;
  4) /mod shield harden включает защитные настройки самого Discord.
"""
import logging
import re
import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from emojis import EMOJI
from utils import can_manage

conn = db.conn
conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS shield_settings (
        guild_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        min_age_days INTEGER NOT NULL DEFAULT 30,
        kick_young INTEGER NOT NULL DEFAULT 1,
        newbie_hours INTEGER NOT NULL DEFAULT 72,
        staff_channel_id INTEGER
    );
    """
)
conn.commit()

DEFAULT_MIN_AGE = 30
LINK_RE = re.compile(r"(https?://|www\.|discord\.gg/|\b[a-z0-9-]+\.(ru|com|net|org|gg|io|xyz|me|su)\b/?)", re.I)
_warned: dict = {}  # (guild_id, user_id) -> когда последний раз предупреждали в чате


def get_settings(guild_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM shield_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def save_settings(guild_id: int, **fields):
    current = get_settings(guild_id) or {
        "enabled": 1, "min_age_days": DEFAULT_MIN_AGE, "kick_young": 1, "newbie_hours": 72, "staff_channel_id": None,
    }
    current.update(fields)
    conn.execute(
        "INSERT OR REPLACE INTO shield_settings (guild_id, enabled, min_age_days, kick_young, newbie_hours, staff_channel_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, current["enabled"], current["min_age_days"], current["kick_young"],
         current["newbie_hours"], current["staff_channel_id"]),
    )
    conn.commit()


async def staff_log(guild: discord.Guild, st: Optional[dict], text: str, member: Optional[discord.Member] = None):
    channel_id = (st or {}).get("staff_channel_id") or db.get_setting(guild.id, "log_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if not channel:
        return
    embed = discord.Embed(description=text, color=0xC0392B)
    if member:
        embed.set_author(name=f"{member} • {member.id}", icon_url=member.display_avatar.url)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


# ---------- вызывается из bot.py ----------
async def handle_join(member: discord.Member) -> bool:
    """Проверяет нового участника. True — участник кикнут, дальше его не приветствуем."""
    guild = member.guild
    st = get_settings(guild.id)
    if st and not st["enabled"]:
        return False
    min_age = st["min_age_days"] if st else DEFAULT_MIN_AGE
    age_days = (discord.utils.utcnow() - member.created_at).days
    if age_days >= min_age:
        return False

    created = int(member.created_at.timestamp())
    if st and st["kick_young"]:
        try:
            await member.send(
                f"Сервер **{guild.name}** принимает только аккаунты старше {min_age} дней. "
                f"Ваш аккаунт создан <t:{created}:R>. Возвращайтесь позже!"
            )
        except discord.HTTPException:
            pass
        try:
            await member.kick(reason=f"Защита: аккаунт моложе {min_age} дней ({age_days} дн.)")
            await staff_log(guild, st, f"**Кикнут новый аккаунт** {member.mention}\n"
                                       f"Создан <t:{created}:R> ({age_days} дн.), порог {min_age} дн.", member)
            return True
        except discord.Forbidden:
            await staff_log(guild, st, f"Не смог кикнуть {member.mention} (аккаунт {age_days} дн.): "
                                       "дайте боту право «Выгонять участников» и поднимите его роль выше.", member)
            return False
    await staff_log(guild, st, f"**Подозрительный вход** {member.mention}\n"
                               f"Аккаунт создан <t:{created}:R> ({age_days} дн.)", member)
    return False


def _has_media(message: discord.Message) -> Optional[str]:
    if message.attachments:
        return "файлы"
    if message.stickers:
        return "стикеры"
    if message.embeds:
        return "встраивания"
    if LINK_RE.search(message.content or ""):
        return "ссылки"
    return None


async def handle_message(message: discord.Message) -> bool:
    """Удаляет медиа и ссылки от новичков. True — сообщение удалено."""
    member = message.author
    if not isinstance(member, discord.Member) or member.joined_at is None:
        return False
    st = get_settings(message.guild.id)
    if not st or not st["enabled"] or st["newbie_hours"] <= 0:
        return False
    if discord.utils.utcnow() - member.joined_at >= timedelta(hours=st["newbie_hours"]):
        return False
    if can_manage(member):
        return False
    what = _has_media(message)
    if not what:
        return False
    try:
        await message.delete()
    except discord.HTTPException:
        return False
    key = (message.guild.id, member.id)
    if time.time() - _warned.get(key, 0) > 60:
        _warned[key] = time.time()
        until = int((member.joined_at + timedelta(hours=st["newbie_hours"])).timestamp())
        try:
            await message.channel.send(
                f"{member.mention}, новичкам пока нельзя отправлять {what}. Станет можно <t:{until}:R>.",
                delete_after=15,
            )
        except discord.HTTPException:
            pass
        await staff_log(message.guild, st, f"Удалено сообщение новичка {member.mention} в {message.channel.mention}: "
                                           f"{what}", member)
    return True


# ---------- команды ----------
class Shield(commands.Cog):
    group = app_commands.Group(
        name="shield", description="Защита от сноса сервера жалобами", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @group.command(name="setup", description="Включить защиту")
    @app_commands.describe(
        staff_channel="Чат персонала для уведомлений",
        min_age_days="Минимальный возраст аккаунта в днях (по умолчанию 30)",
        kick_young="Кикать слишком новые аккаунты (иначе только уведомлять)",
        newbie_hours="Сколько часов новичкам нельзя кидать файлы и ссылки (0 = выкл.)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cmd_setup(self, interaction: discord.Interaction, staff_channel: discord.TextChannel,
                    min_age_days: app_commands.Range[int, 0, 365] = 30, kick_young: bool = True,
                    newbie_hours: app_commands.Range[int, 0, 720] = 72):
        save_settings(interaction.guild_id, enabled=1, staff_channel_id=staff_channel.id, min_age_days=min_age_days,
                      kick_young=int(kick_young), newbie_hours=newbie_hours)
        me = interaction.guild.me
        problems = []
        if kick_young and not me.guild_permissions.kick_members:
            problems.append("• нет права «Выгонять участников»")
        if not me.guild_permissions.manage_messages:
            problems.append("• нет права «Управлять сообщениями»")
        text = (
            f"**Защита включена**\n"
            f"• Аккаунты моложе **{min_age_days} дн.**: {'кик' if kick_young else 'только уведомление'}\n"
            f"• Новичкам первые **{newbie_hours} ч.** нельзя файлы, ссылки, стикеры"
            + (" (выключено)" if newbie_hours == 0 else "") + "\n"
            f"• Уведомления: {staff_channel.mention}\n\n"
            "Теперь запустите `/mod shield harden`: она включит защиту в настройках самого Discord."
        )
        if problems:
            text += f"\n\nБоту не хватает прав:\n" + "\n".join(problems)
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="status", description="Текущие настройки защиты")
    async def status(self, interaction: discord.Interaction):
        st = get_settings(interaction.guild_id)
        g = interaction.guild
        if not st:
            text = (f"Защита не настроена: бот только уведомляет в лог о аккаунтах моложе {DEFAULT_MIN_AGE} дн.\n"
                    "Включите: `/mod shield setup`.")
        else:
            ch = g.get_channel(st["staff_channel_id"]) if st["staff_channel_id"] else None
            text = (
                f"Защита: **{'включена' if st['enabled'] else 'выключена'}**\n"
                f"• Мин. возраст аккаунта: **{st['min_age_days']} дн.** ({'кик' if st['kick_young'] else 'уведомление'})\n"
                f"• Ограничение новичков: **{st['newbie_hours']} ч.**\n"
                f"• Чат персонала: {ch.mention if ch else 'не найден'}"
            )
        text += (
            f"\n\n**Настройки Discord:**\n"
            f"• Уровень проверки: **{g.verification_level.name}** (нужен high или highest)\n"
            f"• Фильтр откровенного контента: **{g.explicit_content_filter.name}** (нужен all_members)\n"
            f"• 2FA для модерации: **{'да' if g.mfa_level else 'нет'}**"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="off", description="Выключить защиту")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def off(self, interaction: discord.Interaction):
        save_settings(interaction.guild_id, enabled=0)
        await interaction.response.send_message("Защита выключена. Включить снова: `/mod shield setup`.", ephemeral=True)

    @group.command(name="harden", description="Включить защитные настройки Discord: проверка, фильтр, AutoMod")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def harden(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        g = interaction.guild
        done, failed = [], []

        try:
            await g.edit(
                verification_level=discord.VerificationLevel.high,
                explicit_content_filter=discord.ContentFilter.all_members,
                reason="Защита /mod shield harden",
            )
            done.append("уровень проверки «Высокий» (аккаунт старше 5 мин. и 10 мин. на сервере)")
            done.append("фильтр откровенного контента для всех участников")
        except discord.HTTPException as e:
            failed.append(f"настройки сервера ({e.text or e})")

        existing = {r.trigger.type for r in await g.fetch_automod_rules()} if g.me.guild_permissions.manage_guild else set()
        rules = [
            ("Анти-спам упоминаний", discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.mention_spam, mention_limit=5),
             "AutoMod: массовые упоминания"),
            ("Анти-спам", discord.AutoModTrigger(type=discord.AutoModRuleTriggerType.spam), "AutoMod: спам"),
            ("Запрещённые слова (Discord)", discord.AutoModTrigger(
                type=discord.AutoModRuleTriggerType.keyword_preset, presets=discord.AutoModPresets.all()),
             "AutoMod: оскорбления, сексуальный контент, ругань"),
        ]
        for name, trigger, label in rules:
            if trigger.type in existing:
                done.append(f"{label} (уже было)")
                continue
            try:
                await g.create_automod_rule(
                    name=name, event_type=discord.AutoModRuleEventType.message_send, trigger=trigger,
                    actions=[discord.AutoModRuleAction(custom_message="Сообщение заблокировано автомодерацией.")],
                    enabled=True, reason="Защита /mod shield harden",
                )
                done.append(label)
            except Exception as e:  # noqa: BLE001 — разные версии discord.py/лимиты Discord
                logging.warning("AutoMod rule %s: %s", name, e)
                failed.append(label)

        text = "**Включено:**\n" + "\n".join(f"{EMOJI['ok']} {x}" for x in done)
        if failed:
            text += "\n\n**Не получилось** (нужно право «Управлять сервером» у бота):\n" + "\n".join(f"{EMOJI['no']} {x}" for x in failed)
        text += (
            "\n\n**Вручную (бот не может):**\n"
            "• Настройки сервера → Безопасность → **требовать 2FA для модерации** (может только владелец)\n"
            "• Всем модераторам включить 2FA в своём аккаунте\n"
            "• Не выдавать «Администратор» лишним людям"
        )
        await interaction.followup.send(text[:1990], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shield(bot))
