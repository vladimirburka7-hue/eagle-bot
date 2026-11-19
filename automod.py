"""Автомодерация: мат и оскорбления в чате -> сообщение удаляется, участник получает мут (таймаут).

Персоналу: команды /mod (выдать, продлить, снять, история), настройка /mod automod и
«центр управления» одним сообщением в чате персонала (/mod center).
"""
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from badwords import censor, find_violation, mask
from content import (
    CATEGORY_NAMES, build_button_payload, build_modcenter_payload, build_modlog_payload, edit_components,
    post_components, respond_components,
)
from emojis import EMOJI
from utils import can_manage

MAX_MINUTES = 28 * 24 * 60  # предел таймаута в Discord
DEFAULT_CATEGORIES = ("mat", "insult", "hate", "threat")
TARGETS = {}  # (сервер, модератор) -> выбранный в центре управления участник
NO_PING = discord.AllowedMentions.none()


# ---------- Вспомогательное ----------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def current_until(member: discord.Member) -> Optional[datetime]:
    """До какого времени участник в муте (None, если мута нет)."""
    until = getattr(member, "timed_out_until", None)
    return until if until and until > utcnow() else None


def left_text(until: datetime) -> str:
    minutes = max(1, int((until - utcnow()).total_seconds() // 60) + 1)
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"


def off_categories(settings: dict) -> set:
    """Категории готового списка, которые на сервере выключены."""
    return {c for c in (settings.get("off_categories") or "").split(",") if c}


def categories_for(settings: dict) -> tuple:
    off = off_categories(settings)
    cats = tuple(c for c in DEFAULT_CATEGORIES if c not in off)
    return cats + (("mild",) if settings.get("include_mild") else ())


def staff_only(interaction: discord.Interaction) -> bool:
    return can_manage(interaction.user)


async def apply_timeout(member: discord.Member, until: datetime, reason: str):
    await member.timeout(until, reason=reason[:480])


def mute_error(e: Exception) -> str:
    if isinstance(e, discord.Forbidden):
        return ("Не хватает прав: выдайте боту право «Модерировать участников» и поставьте его роль выше роли "
                "участника (администраторам и владельцу мут выдать нельзя).")
    return f"Не удалось выдать мут: {e}"


# ---------- Действия ----------
async def do_mute(bot, guild: discord.Guild, member: discord.Member, minutes: int, moderator_id: int, reason: str, source="manual"):
    """Выдаёт мут на minutes минут от текущего момента. Возвращает (until, ошибка)."""
    until = utcnow() + timedelta(minutes=minutes)
    try:
        await apply_timeout(member, until, reason or "Мут")
    except discord.HTTPException as e:
        return None, mute_error(e)
    db.add_mod_action(guild.id, member.id, moderator_id, "mute", minutes, reason, source, int(time.time()), int(until.timestamp()))
    await refresh_center(bot, guild)
    return until, None


async def do_extend(bot, guild: discord.Guild, member: discord.Member, minutes: int, moderator_id: int):
    """Продлевает действующий мут на minutes минут (если мута нет, выдаёт новый)."""
    base = current_until(member) or utcnow()
    until = min(base + timedelta(minutes=minutes), utcnow() + timedelta(minutes=MAX_MINUTES))
    try:
        await apply_timeout(member, until, f"Продление мута на {minutes} мин")
    except discord.HTTPException as e:
        return None, mute_error(e)
    db.add_mod_action(guild.id, member.id, moderator_id, "extend", minutes, "", "manual", int(time.time()), int(until.timestamp()))
    await refresh_center(bot, guild)
    return until, None


async def do_lift(bot, guild: discord.Guild, member: discord.Member, moderator_id: int):
    if current_until(member) is None:
        return False, "У участника нет активного мута."
    try:
        await member.timeout(None, reason="Мут снят")
    except discord.HTTPException as e:
        return False, mute_error(e)
    db.add_mod_action(guild.id, member.id, moderator_id, "unmute", None, "", "manual", int(time.time()), None)
    await refresh_center(bot, guild)
    return True, None


async def refresh_center(bot, guild: discord.Guild):
    """Обновляет сообщение центра управления (счётчики и настройки)."""
    st = db.get_automod_settings(guild.id)
    if not st or not st["center_channel_id"] or not st["center_message_id"]:
        return
    active = sum(1 for m in guild.members if current_until(m))
    payload = build_modcenter_payload(
        active=active, today=db.count_mod_actions_since(guild.id, int(time.time()) - 86400), enabled=bool(st["enabled"]),
        mute_minutes=st["mute_minutes"], delete_messages=bool(st["delete_messages"]),
        extra_words=len(db.list_automod_words(guild.id, "deny")), reading_ok=bool(getattr(bot.intents, "message_content", False)),
    )
    try:
        await edit_components(bot, st["center_channel_id"], st["center_message_id"], payload)
    except discord.HTTPException:
        logging.warning("Не удалось обновить центр управления")


def card_payload(action: dict, status: str, closed: bool = False) -> dict:
    return build_modlog_payload(
        user_id=action["user_id"], channel_id=action["channel_id"] or 0, category=action["category"] or "custom",
        word=action["word"] or "", excerpt=action["excerpt"] or "", minutes=action["minutes"] or 0,
        until_ts=action["until"] or int(time.time()), status=status, closed=closed,
    )


# ---------- Формы ----------
class MuteModal(discord.ui.Modal, title="Выдать мут"):
    def __init__(self, target_id: int):
        super().__init__(timeout=300)
        self.target_id = target_id
        self.minutes = discord.ui.TextInput(label="На сколько минут", default="15", max_length=5)
        self.reason = discord.ui.TextInput(label="Причина (необязательно)", required=False, max_length=200)
        self.add_item(self.minutes)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        try:
            minutes = int(self.minutes.value.strip())
            if not 1 <= minutes <= MAX_MINUTES:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(f"Нужно число минут от 1 до {MAX_MINUTES}.", ephemeral=True)
            return
        member = interaction.guild.get_member(self.target_id)
        if member is None:
            await interaction.response.send_message("Участник не найден на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        until, error = await do_mute(interaction.client, interaction.guild, member, minutes, interaction.user.id,
                                     self.reason.value.strip() or "Мут от персонала")
        await interaction.followup.send(error or f"{member.mention} в муте до <t:{int(until.timestamp())}:t> ({minutes} мин).",
                                        ephemeral=True, allowed_mentions=NO_PING)


class ExtendModal(discord.ui.Modal, title="Продлить мут"):
    def __init__(self, target_id: int):
        super().__init__(timeout=300)
        self.target_id = target_id
        self.minutes = discord.ui.TextInput(label="На сколько минут продлить", default="15", max_length=5)
        self.add_item(self.minutes)

    async def on_submit(self, interaction: discord.Interaction):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        try:
            minutes = int(self.minutes.value.strip())
            if not 1 <= minutes <= MAX_MINUTES:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(f"Нужно число минут от 1 до {MAX_MINUTES}.", ephemeral=True)
            return
        member = interaction.guild.get_member(self.target_id)
        if member is None:
            await interaction.response.send_message("Участник не найден на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        until, error = await do_extend(interaction.client, interaction.guild, member, minutes, interaction.user.id)
        await interaction.followup.send(error or f"Мут {member.mention} продлён до <t:{int(until.timestamp())}:t>.",
                                        ephemeral=True, allowed_mentions=NO_PING)


# ---------- Центр управления ----------
async def need_target(interaction: discord.Interaction):
    """Выбранный в центре участник или None (с подсказкой)."""
    if not staff_only(interaction):
        await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
        return None
    target_id = TARGETS.get((interaction.guild_id, interaction.user.id))
    member = interaction.guild.get_member(target_id) if target_id else None
    if member is None:
        await interaction.response.send_message("Сначала выберите участника в меню над кнопками.", ephemeral=True)
        return None
    return member


class CenterSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="mc:user", placeholder="Выберите участника для действия")

    async def callback(self, interaction: discord.Interaction):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        member = interaction.guild.get_member(self.values[0].id)
        if member is None:
            await interaction.response.send_message("Участник не найден на сервере.", ephemeral=True)
            return
        TARGETS[(interaction.guild_id, interaction.user.id)] = member.id
        until = current_until(member)
        state = f"в муте, осталось {left_text(until)}" if until else "мута нет"
        await interaction.response.send_message(
            f"Выбран {member.mention} ({state}). Нажмите «Выдать мут», «Продлить», «Снять мут» или «История».",
            ephemeral=True, allowed_mentions=NO_PING,
        )


class CenterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(CenterSelect())

    @discord.ui.button(label="Выдать мут", custom_id="mc:mute", style=discord.ButtonStyle.danger, row=1)
    async def mute(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await need_target(interaction)
        if member:
            await interaction.response.send_modal(MuteModal(member.id))

    @discord.ui.button(label="Продлить", custom_id="mc:extend", style=discord.ButtonStyle.primary, row=1)
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await need_target(interaction)
        if member:
            await interaction.response.send_modal(ExtendModal(member.id))

    @discord.ui.button(label="Снять мут", custom_id="mc:lift", style=discord.ButtonStyle.success, row=1)
    async def lift(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await need_target(interaction)
        if member:
            await interaction.response.defer(ephemeral=True)
            ok, error = await do_lift(interaction.client, interaction.guild, member, interaction.user.id)
            await interaction.followup.send(error or f"Мут {member.mention} снят.", ephemeral=True, allowed_mentions=NO_PING)

    @discord.ui.button(label="История", custom_id="mc:hist", row=1)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = await need_target(interaction)
        if member:
            await interaction.response.send_message(history_text(interaction.guild_id, member), ephemeral=True, allowed_mentions=NO_PING)

    @discord.ui.button(label="Активные муты", custom_id="mc:active", row=2)
    async def active(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        rows = sorted(((current_until(m), m) for m in interaction.guild.members if current_until(m)), key=lambda x: x[0])
        lines = [f"• {m.mention} · осталось {left_text(u)} (до <t:{int(u.timestamp())}:t>)" for u, m in rows[:25]]
        await interaction.response.send_message("\n".join(lines) or "Сейчас никто не в муте.", ephemeral=True, allowed_mentions=NO_PING)

    @discord.ui.button(label="Автомодерация", custom_id="mc:toggle", row=2)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        st = db.get_automod_settings(interaction.guild_id) or {}
        new = 0 if st.get("enabled", 1) else 1
        db.set_automod_settings(interaction.guild_id, enabled=new)
        await interaction.response.defer(ephemeral=True)
        await refresh_center(interaction.client, interaction.guild)
        await interaction.followup.send("Автомодерация включена." if new else "Автомодерация выключена.", ephemeral=True)

    @discord.ui.button(label="Слова", custom_id="mc:words", row=2)
    async def words(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await show_words(interaction)

    @discord.ui.button(label="Обновить", custom_id="mc:refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await refresh_center(interaction.client, interaction.guild)
        await interaction.followup.send("Обновлено.", ephemeral=True)


def history_text(guild_id: int, member: discord.Member) -> str:
    rows = db.list_mod_actions(guild_id, member.id)
    names = {"mute": "мут", "extend": "продление", "unmute": "снятие мута"}
    lines = [f"• <t:{r['created_at']}:d> {names.get(r['action'], r['action'])}"
             + (f" {r['minutes']} мин" if r["minutes"] else "")
             + (" (автоматически)" if r["source"] == "auto" else "")
             + (f": {r['reason']}" if r["reason"] else "") for r in rows]
    until = current_until(member)
    head = f"**{member.display_name}**: " + (f"в муте, осталось {left_text(until)}" if until else "сейчас без мута")
    return head + "\n" + ("\n".join(lines) or "Записей нет.")


async def show_words(interaction: discord.Interaction):
    st = db.get_automod_settings(interaction.guild_id) or {}
    deny, allow = db.list_automod_words(interaction.guild_id, "deny"), db.list_automod_words(interaction.guild_id, "allow")
    cats = categories_for(st)
    sections = [
        ("Что проверяется", "\n".join(
            f"{CATEGORY_NAMES.get(c, c).capitalize()}: {'включено' if c in cats else 'выключено'}"
            for c in DEFAULT_CATEGORIES + ("mild",)) + "\nИзменить: /mod automod category"),
        ("Свои слова", (", ".join(deny[:40]) or "пока нет") + "\nДобавить: /mod automod word_add"),
        ("Исключения", (", ".join(allow[:40]) or "пока нет") + "\nДобавить: /mod automod word_add с параметром allow"),
    ]
    payload = build_button_payload(title="Слова автомодерации", sections=sections)
    try:
        await respond_components(interaction.client, interaction, payload)
    except discord.HTTPException:
        await interaction.response.send_message("\n\n".join(f"{h}\n{b}" for h, b in sections)[:1900], ephemeral=True)


# ---------- Карточка нарушения в логе ----------
class LogView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _act(self, interaction: discord.Interaction, kind: str):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        action = db.get_mod_action_by_message(interaction.message.id)
        if not action:
            await interaction.response.send_message("Запись об этом нарушении не найдена.", ephemeral=True)
            return
        member = interaction.guild.get_member(action["user_id"])
        if member is None:
            await interaction.response.send_message("Участника уже нет на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        who = interaction.user.display_name
        if kind == "lift":
            ok, error = await do_lift(interaction.client, interaction.guild, member, interaction.user.id)
            if not ok:
                await interaction.followup.send(error, ephemeral=True)
                return
            db.update_mod_action(action["id"], status="снят")
            payload = card_payload(db.get_mod_action(action["id"]), f"мут снят ({who})", closed=True)
        else:
            minutes = 15 if kind == "ext15" else 60
            until, error = await do_extend(interaction.client, interaction.guild, member, minutes, interaction.user.id)
            if error:
                await interaction.followup.send(error, ephemeral=True)
                return
            db.update_mod_action(action["id"], until=int(until.timestamp()), minutes=(action["minutes"] or 0) + minutes)
            payload = card_payload(db.get_mod_action(action["id"]), f"продлён на {minutes} мин ({who})")
        try:
            await edit_components(interaction.client, interaction.channel_id, interaction.message.id, payload)
        except discord.HTTPException:
            pass
        await interaction.followup.send("Готово.", ephemeral=True)

    @discord.ui.button(label="+15 минут", custom_id="am:ext15", style=discord.ButtonStyle.primary)
    async def ext15(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._act(interaction, "ext15")

    @discord.ui.button(label="+1 час", custom_id="am:ext60", style=discord.ButtonStyle.primary)
    async def ext60(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._act(interaction, "ext60")

    @discord.ui.button(label="Снять мут", custom_id="am:lift", style=discord.ButtonStyle.success)
    async def lift(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._act(interaction, "lift")


# ---------- Модуль ----------
class AutoMod(commands.Cog):
    mod = app_commands.Group(name="mod", description="Муты и центр управления (для персонала)", guild_only=True)
    setting = app_commands.Group(name="automod_setting", description="Настройки автомодерации", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not getattr(self.bot.intents, "message_content", False):
            logging.warning("Автомодерация не видит текст сообщений: включите Message Content Intent в Developer Portal "
                            "и добавьте USE_MESSAGE_CONTENT=1 в .env")
        self.bot.add_view(CenterView())
        self.bot.add_view(LogView())
        self.keep_fresh.start()

    async def cog_unload(self):
        self.keep_fresh.cancel()

    @tasks.loop(minutes=5)
    async def keep_fresh(self):
        for st in db.list_automod_centers():
            guild = self.bot.get_guild(st["guild_id"])
            if guild:
                await refresh_center(self.bot, guild)

    @keep_fresh.before_loop
    async def before_keep_fresh(self):
        await self.bot.wait_until_ready()

    # ----- Проверка сообщений -----
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not message.content:
            return
        await self.check_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.guild is None or before.content == after.content or not after.content:
            return
        await self.check_message(after)

    def exempt(self, message: discord.Message, member: discord.Member) -> bool:
        if can_manage(member) or getattr(member.guild_permissions, "administrator", False):
            return True
        guild_id = message.guild.id
        if message.channel.id in db.list_automod_exempt(guild_id, "channel"):
            return True
        roles = set(db.list_automod_exempt(guild_id, "role"))
        return any(r.id in roles for r in getattr(member, "roles", []))

    async def check_message(self, message: discord.Message):
        st = db.get_automod_settings(message.guild.id)
        member = message.author
        if not st or not st["enabled"] or not isinstance(member, discord.Member) or self.exempt(message, member):
            return
        hit = find_violation(message.content, categories_for(st), db.list_automod_words(message.guild.id, "deny"),
                             db.list_automod_words(message.guild.id, "allow"))
        if not hit:
            return
        category, word = hit
        if st["delete_messages"]:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        if current_until(member):  # уже в муте: сообщение удалили, второй мут не нужен
            return
        minutes = st["mute_minutes"]
        until, error = await do_mute(self.bot, message.guild, member, minutes, self.bot.user.id if self.bot.user else 0,
                                     f"Автомодерация: {CATEGORY_NAMES.get(category, category)}", source="auto")
        log_channel = message.guild.get_channel(st["log_channel_id"] or st["center_channel_id"] or 0)
        if error:
            if log_channel:
                await log_channel.send(f"Не удалось выдать мут {member.mention}: {error}", allowed_mentions=NO_PING)
            return
        try:
            await message.channel.send(
                f"{member.mention} — мут {minutes} мин за нарушение правил.",
                delete_after=20, allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            pass
        try:
            await member.send(f"Вы получили мут на {minutes} мин на сервере **{message.guild.name}** за нарушение правил общения.")
        except discord.HTTPException:
            pass
        action_id = db.add_mod_action(
            message.guild.id, member.id, self.bot.user.id if self.bot.user else 0, "auto", minutes,
            f"{category}: {mask(word)}", "auto", int(time.time()), int(until.timestamp()), category, mask(word),
            censor(message.content, word)[:500], message.channel.id,
        )
        if log_channel:
            action = db.get_mod_action(action_id)
            try:
                card = await post_components(self.bot, log_channel, card_payload(action, "мут активен"))
                db.update_mod_action(action_id, message_id=int(card["id"]))
            except discord.HTTPException:
                logging.warning("Не удалось отправить карточку нарушения")

    # ----- Команды /mod -----
    @mod.command(name="mute", description="Выдать мут участнику")
    @app_commands.describe(user="Кому", minutes="На сколько минут (по умолчанию 15)", reason="Причина (необязательно)")
    async def mod_mute(self, interaction: discord.Interaction, user: discord.Member,
                       minutes: app_commands.Range[int, 1, 40320] = 15, reason: str = ""):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        until, error = await do_mute(self.bot, interaction.guild, user, minutes, interaction.user.id, reason or "Мут от персонала")
        await interaction.followup.send(error or f"{user.mention} в муте до <t:{int(until.timestamp())}:t> ({minutes} мин).",
                                        ephemeral=True, allowed_mentions=NO_PING)

    @mod.command(name="extend", description="Продлить мут участнику")
    @app_commands.describe(user="Кому", minutes="На сколько минут продлить (по умолчанию 15)")
    async def mod_extend(self, interaction: discord.Interaction, user: discord.Member, minutes: app_commands.Range[int, 1, 40320] = 15):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        until, error = await do_extend(self.bot, interaction.guild, user, minutes, interaction.user.id)
        await interaction.followup.send(error or f"Мут {user.mention} продлён до <t:{int(until.timestamp())}:t>.",
                                        ephemeral=True, allowed_mentions=NO_PING)

    @mod.command(name="unmute", description="Снять мут с участника")
    @app_commands.describe(user="С кого снять")
    async def mod_unmute(self, interaction: discord.Interaction, user: discord.Member):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        ok, error = await do_lift(self.bot, interaction.guild, user, interaction.user.id)
        await interaction.followup.send(error or f"Мут {user.mention} снят.", ephemeral=True, allowed_mentions=NO_PING)

    @mod.command(name="history", description="История мутов участника")
    @app_commands.describe(user="Чью историю показать")
    async def mod_history(self, interaction: discord.Interaction, user: discord.Member):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.send_message(history_text(interaction.guild_id, user), ephemeral=True, allowed_mentions=NO_PING)

    @mod.command(name="center", description="Отправить в чат персонала центр управления")
    @app_commands.describe(channel="Канал для центра управления (по умолчанию текущий)")
    async def mod_center(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        st = db.get_automod_settings(interaction.guild_id) or {}
        old = st.get("center_channel_id"), st.get("center_message_id")
        if old[0] and old[1]:
            old_channel = interaction.guild.get_channel(old[0])
            if old_channel:
                try:
                    await (await old_channel.fetch_message(old[1])).delete()
                except discord.HTTPException:
                    pass
        payload = build_modcenter_payload(
            active=0, today=0, enabled=bool(st.get("enabled", 1)), mute_minutes=st.get("mute_minutes", 15),
            delete_messages=bool(st.get("delete_messages", 1)), extra_words=len(db.list_automod_words(interaction.guild_id, "deny")),
            reading_ok=bool(getattr(self.bot.intents, "message_content", False)),
        )
        try:
            message = await post_components(self.bot, target, payload)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить в {target.mention}: {e}", ephemeral=True)
            return
        db.set_automod_settings(interaction.guild_id, center_channel_id=target.id, center_message_id=int(message["id"]))
        await refresh_center(self.bot, interaction.guild)
        await interaction.followup.send(f"Центр управления отправлен в {target.mention}.", ephemeral=True)

    # ----- Настройки -----
    @setting.command(name="setup", description="Включить автомодерацию: канал логов, длительность мута")
    @app_commands.describe(
        log_channel="Канал персонала, куда приходят карточки нарушений",
        mute_minutes="Длительность мута за нарушение в минутах (по умолчанию 15)",
        delete_messages="Удалять сообщение с нарушением",
        enabled="Включить автомодерацию",
        include_mild="Проверять и мягкие слова (дурак, лох и т.п.)",
    )
    async def automod_setup(self, interaction: discord.Interaction, log_channel: discord.TextChannel,
                            mute_minutes: app_commands.Range[int, 1, 1440] = 15, delete_messages: bool = True,
                            enabled: bool = True, include_mild: bool = False):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        me = interaction.guild.me.guild_permissions
        warnings = []
        if not me.moderate_members:
            warnings.append("Выдайте боту право «Модерировать участников» и поставьте его роль выше ролей участников.")
        if delete_messages and not me.manage_messages:
            warnings.append("Для удаления сообщений боту нужно право «Управление сообщениями».")
        if not getattr(self.bot.intents, "message_content", False):
            warnings.append("Бот не видит текст сообщений: включите Message Content Intent в Developer Portal и "
                            "добавьте USE_MESSAGE_CONTENT=1 в .env, затем перезапустите бота.")
        db.set_automod_settings(interaction.guild_id, log_channel_id=log_channel.id, mute_minutes=mute_minutes,
                                delete_messages=int(delete_messages), enabled=int(enabled), include_mild=int(include_mild))
        await interaction.response.defer(ephemeral=True)
        await refresh_center(self.bot, interaction.guild)
        await interaction.followup.send(
            f"Автомодерация {'включена' if enabled else 'выключена'}. Мут {mute_minutes} мин, карточки в {log_channel.mention}.\n"
            + ("\n".join(warnings) if warnings else "Права и чтение сообщений в порядке."), ephemeral=True, allowed_mentions=NO_PING)

    @setting.command(name="category", description="Включить или выключить категорию готового списка слов")
    @app_commands.describe(category="Какую категорию", enabled="Проверять эту категорию")
    @app_commands.choices(category=[
        app_commands.Choice(name="Мат", value="mat"),
        app_commands.Choice(name="Оскорбления", value="insult"),
        app_commands.Choice(name="Унижение по признаку", value="hate"),
        app_commands.Choice(name="Угрозы", value="threat"),
        app_commands.Choice(name="Мягкие слова (дурак, лох…)", value="mild"),
    ])
    async def automod_category(self, interaction: discord.Interaction, category: app_commands.Choice[str], enabled: bool):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        st = db.get_automod_settings(interaction.guild_id)
        if not st:
            await interaction.response.send_message("Сначала включите автомодерацию: /mod automod setup", ephemeral=True)
            return
        if category.value == "mild":
            db.set_automod_settings(interaction.guild_id, include_mild=int(enabled))
        else:
            off = off_categories(st)
            (off.discard if enabled else off.add)(category.value)
            db.set_automod_settings(interaction.guild_id, off_categories=",".join(sorted(off)))
        await interaction.response.send_message(
            f"{category.name}: {'проверяется' if enabled else 'больше не проверяется'}.", ephemeral=True)

    @setting.command(name="word_add", description="Добавить своё запрещённое слово (или слово-исключение)")
    @app_commands.describe(word="Слово", allow="Не запрещать это слово, даже если оно есть в общем списке")
    async def automod_word_add(self, interaction: discord.Interaction, word: str, allow: bool = False):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        word = " ".join(word.lower().split())[:40]
        if not word:
            await interaction.response.send_message("Слово не может быть пустым.", ephemeral=True)
            return
        db.add_automod_word(interaction.guild_id, word, "allow" if allow else "deny")
        await interaction.response.defer(ephemeral=True)
        await refresh_center(self.bot, interaction.guild)
        await interaction.followup.send(f"«{word}» добавлено в {'исключения' if allow else 'запрещённые слова'}.", ephemeral=True)

    @setting.command(name="word_remove", description="Убрать своё слово или исключение из списка")
    @app_commands.describe(word="Слово")
    async def automod_word_remove(self, interaction: discord.Interaction, word: str):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        removed = db.remove_automod_word(interaction.guild_id, " ".join(word.lower().split())[:40])
        await interaction.response.send_message("Убрано." if removed else "Такого слова в ваших списках нет.", ephemeral=True)

    @setting.command(name="words", description="Показать свои слова, исключения и что проверяется")
    async def automod_words(self, interaction: discord.Interaction):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await show_words(interaction)

    @setting.command(name="exempt", description="Не проверять роль или канал (повторно: вернуть проверку)")
    @app_commands.describe(role="Роль, которую не проверять", channel="Канал, в котором не проверять")
    async def automod_exempt(self, interaction: discord.Interaction, role: Optional[discord.Role] = None,
                             channel: Optional[discord.abc.GuildChannel] = None):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        if not role and not channel:
            await interaction.response.send_message("Укажите роль или канал.", ephemeral=True)
            return
        lines = []
        if role:
            on = db.toggle_automod_exempt(interaction.guild_id, role.id, "role")
            lines.append(f"Роль {role.mention}: {'не проверяется' if on else 'проверяется снова'}.")
        if channel:
            on = db.toggle_automod_exempt(interaction.guild_id, channel.id, "channel")
            lines.append(f"Канал {channel.mention}: {'не проверяется' if on else 'проверяется снова'}.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_PING)

    @setting.command(name="test", description="Проверить, как автомодерация отнесётся к фразе")
    @app_commands.describe(text="Фраза для проверки")
    async def automod_test(self, interaction: discord.Interaction, text: str):
        if not staff_only(interaction):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        st = db.get_automod_settings(interaction.guild_id) or {}
        hit = find_violation(text, categories_for(st), db.list_automod_words(interaction.guild_id, "deny"),
                             db.list_automod_words(interaction.guild_id, "allow"))
        answer = (f"Нарушение: {CATEGORY_NAMES.get(hit[0], hit[0])} (`{mask(hit[1])}`). Участник получил бы мут."
                  if hit else "Нарушений не найдено.")
        await interaction.response.send_message(answer, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
