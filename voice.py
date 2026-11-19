"""Свои временные голосовые комнаты с панелью управления (как TempVoice).

Заходите в канал создания, бот делает вашу комнату и переносит вас. В чате комнаты панель:
название, лимит, доступ (открыта / закрыта / приватная), доверенные, блокировка, передача.
Когда все выходят, комната удаляется сама.
"""
import asyncio
import os
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from emojis import EMOJI
from content import (
    build_button_payload, build_hub_payload, build_voice_panel_payload, edit_components, post_components,
    respond_components,
)
from utils import (
    can_manage, clean_room_name, rename_allowed, voice_everyone_perms, voice_next_state, voice_prefix,
)

DEFAULT_TEMPLATE = "၊၊||၊・голосовой канал"
CREATE_COOLDOWN = 8  # секунд между созданиями комнат одним человеком
RENAME_LOG = {}      # channel_id -> времена переименований
LAST_CREATE = {}     # user_id -> время последнего создания
PENDING_DELETE = set()


# ---------- Вспомогательное ----------
def room_overwrites(guild: discord.Guild, owner: discord.Member, state: str) -> dict:
    PO = discord.PermissionOverwrite
    view, connect = voice_everyone_perms(state)
    return {
        guild.default_role: PO(view_channel=view, connect=connect),
        guild.me: PO(view_channel=True, connect=True, speak=True, manage_channels=True, move_members=True,
                     send_messages=True, embed_links=True, read_message_history=True),
        owner: PO(view_channel=True, connect=True, speak=True, stream=True),
    }


def room_counts(channel: discord.VoiceChannel, owner_id: int, me_id: int):
    trusted = banned = 0
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role) or target.id in (owner_id, me_id):
            continue
        if overwrite.connect is True:
            trusted += 1
        elif overwrite.connect is False:
            banned += 1
    return trusted, banned


async def refresh_panel(bot, guild: discord.Guild, room: dict):
    channel = guild.get_channel(room["channel_id"])
    if channel is None or not room["panel_message_id"]:
        return
    trusted, banned = room_counts(channel, room["owner_id"], guild.me.id)
    payload = build_voice_panel_payload(
        owner_id=room["owner_id"], name=channel.name, state=room["state"],
        limit=channel.user_limit, trusted=trusted, banned=banned,
    )
    try:
        await edit_components(bot, channel.id, room["panel_message_id"], payload)
    except discord.HTTPException:
        logging.warning("Не удалось обновить панель комнаты %s", channel.id)


_ASSETS = {}


def load_asset(name: str):
    """Картинка из папки assets (кэшируется). Если файла нет, сообщение идёт без неё."""
    if name not in _ASSETS:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name), "rb") as f:
                _ASSETS[name] = f.read()
        except OSError:
            _ASSETS[name] = None
    return _ASSETS[name]


def in_room(member: discord.Member, channel: discord.VoiceChannel) -> bool:
    return bool(member.voice and member.voice.channel and member.voice.channel.id == channel.id)


async def get_room(interaction: discord.Interaction, owner_only: bool = True):
    """Комната по чату, в котором нажата кнопка. Управлять может владелец или персонал."""
    room = db.get_voice_room(interaction.channel_id)
    if not room:  # кнопка нажата не в чате комнаты (например, в канале с общим сообщением): берём комнату владельца
        room = db.get_voice_room_by_owner(interaction.guild_id, interaction.user.id)
    if not room:
        await interaction.response.send_message("У вас нет своей комнаты. Зайдите в канал создания комнат.", ephemeral=True)
        return None
    if owner_only and interaction.user.id != room["owner_id"] and not can_manage(interaction.user):
        await interaction.response.send_message(
            "Управлять комнатой может её владелец.", ephemeral=True
        )
        return None
    return room


async def delete_room(bot, guild: discord.Guild, channel_id: int):
    db.delete_voice_room(channel_id)
    channel = guild.get_channel(channel_id) if guild else None
    if channel:
        try:
            await channel.delete(reason="Временная комната удалена")
        except discord.HTTPException:
            logging.warning("Не удалось удалить комнату %s", channel_id)


# ---------- Формы ----------
class NameModal(discord.ui.Modal, title="Название комнаты"):
    def __init__(self, current: str, prefix: str):
        super().__init__(timeout=300)
        self.prefix = prefix
        self.text = discord.ui.TextInput(
            label="Новое название", max_length=60,
            default=(current[len(prefix):] if prefix and current.startswith(prefix) else current)[:60],
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        name = clean_room_name(self.prefix, self.text.value)
        if not name:
            await interaction.response.send_message("Название не может быть пустым.", ephemeral=True)
            return
        ok, recent, wait = rename_allowed(RENAME_LOG.get(room["channel_id"], []), time.time())
        RENAME_LOG[room["channel_id"]] = recent
        if not ok:
            await interaction.response.send_message(
                f"Discord позволяет переименовывать канал не чаще двух раз за 10 минут. Подождите {wait // 60 + 1} мин.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(room["channel_id"])
        try:
            await channel.edit(name=name, reason="Переименование комнаты")
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось переименовать: {e}", ephemeral=True)
            return
        RENAME_LOG[room["channel_id"]].append(time.time())
        db.set_voice_prefs(interaction.guild_id, room["owner_id"], name=name)
        await refresh_panel(interaction.client, interaction.guild, room)
        await interaction.followup.send("Название изменено.", ephemeral=True)


class LimitModal(discord.ui.Modal, title="Лимит участников"):
    def __init__(self, current: int):
        super().__init__(timeout=300)
        self.number = discord.ui.TextInput(label="Сколько человек (0 = без лимита)", max_length=2, default=str(current or 0))
        self.add_item(self.number)

    async def on_submit(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        try:
            value = int(self.number.value.strip())
            if not 0 <= value <= 99:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Нужно число от 0 до 99.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(room["channel_id"])
        try:
            await channel.edit(user_limit=value, reason="Лимит комнаты")
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось изменить лимит: {e}", ephemeral=True)
            return
        db.set_voice_prefs(interaction.guild_id, room["owner_id"], user_limit=value)
        await refresh_panel(interaction.client, interaction.guild, room)
        await interaction.followup.send(f"Лимит: {value or 'без лимита'}.", ephemeral=True)


class ConfirmDelete(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=60)
        self.channel_id = channel_id

    @discord.ui.button(label="Да, удалить комнату", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = db.get_voice_room(self.channel_id)
        if not room or (interaction.user.id != room["owner_id"] and not can_manage(interaction.user)):
            await interaction.response.edit_message(content="Нет прав или комната уже удалена.", view=None)
            return
        await interaction.response.edit_message(content="Удаляю комнату…", view=None)
        await delete_room(interaction.client, interaction.guild, self.channel_id)


# ---------- Выбор участника ----------
class TrustSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="vc:trust", placeholder="Доверять (повторно: убрать доверие)")

    async def callback(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        guild = interaction.guild
        member = guild.get_member(self.values[0].id)
        channel = guild.get_channel(room["channel_id"])
        if member is None or member.bot or member.id == room["owner_id"]:
            await interaction.response.send_message("Этого участника выбрать нельзя.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if channel.overwrites_for(member).connect is True:
            await channel.set_permissions(member, overwrite=None, reason="Доверие снято")
            text = f"{member.mention} больше не в списке доверенных."
        else:
            await channel.set_permissions(member, view_channel=True, connect=True, reason="Доверенный участник")
            text = f"{member.mention} теперь может заходить даже в закрытую комнату."
        await refresh_panel(interaction.client, guild, room)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class KickSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="vc:kick", placeholder="Выгнать из комнаты")

    async def callback(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        guild = interaction.guild
        member = guild.get_member(self.values[0].id)
        channel = guild.get_channel(room["channel_id"])
        if member is None or member.id == room["owner_id"] or member.bot:
            await interaction.response.send_message("Этого участника выгнать нельзя.", ephemeral=True)
            return
        if not in_room(member, channel):
            await interaction.response.send_message("Этого участника сейчас нет в комнате.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await member.move_to(None, reason="Выгнан из временной комнаты")
        except discord.HTTPException:
            await interaction.followup.send("Не удалось выгнать: у бота нет права «Перемещать участников».", ephemeral=True)
            return
        await interaction.followup.send(f"{member.mention} выгнан(а) из комнаты.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class BanSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="vc:ban", placeholder="Заблокировать (повторно: разблокировать)")

    async def callback(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        guild = interaction.guild
        member = guild.get_member(self.values[0].id)
        channel = guild.get_channel(room["channel_id"])
        if member is None or member.id == room["owner_id"] or member.bot:
            await interaction.response.send_message("Этого участника заблокировать нельзя.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        if channel.overwrites_for(member).connect is False:
            await channel.set_permissions(member, overwrite=None, reason="Разблокирован")
            text = f"{member.mention} разблокирован(а)."
        else:
            await channel.set_permissions(member, view_channel=False, connect=False, reason="Заблокирован в комнате")
            if in_room(member, channel):
                try:
                    await member.move_to(None, reason="Заблокирован в комнате")
                except discord.HTTPException:
                    pass
            text = f"{member.mention} заблокирован(а) в этой комнате."
        await refresh_panel(interaction.client, guild, room)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class OwnerSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="vc:owner", placeholder="Передать комнату")

    async def callback(self, interaction: discord.Interaction):
        room = await get_room(interaction)
        if not room:
            return
        guild = interaction.guild
        member = guild.get_member(self.values[0].id)
        channel = guild.get_channel(room["channel_id"])
        if member is None or member.bot or member.id == room["owner_id"]:
            await interaction.response.send_message("Выберите другого участника (не бота и не владельца).", ephemeral=True)
            return
        if not in_room(member, channel):
            await interaction.response.send_message("Передать можно только тому, кто сейчас в комнате.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await transfer_owner(interaction.client, guild, channel, room, member)
        await interaction.followup.send(f"Новый владелец: {member.mention}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def transfer_owner(bot, guild: discord.Guild, channel: discord.VoiceChannel, room: dict, new_owner: discord.Member):
    old = guild.get_member(room["owner_id"])
    if old:
        await channel.set_permissions(old, view_channel=True, connect=True, reason="Прежний владелец стал доверенным")
    await channel.set_permissions(new_owner, view_channel=True, connect=True, speak=True, stream=True, reason="Новый владелец комнаты")
    db.update_voice_room(room["channel_id"], owner_id=new_owner.id)
    room = db.get_voice_room(room["channel_id"])
    await refresh_panel(bot, guild, room)


# ---------- Панель ----------
class PanelView(discord.ui.View):
    """Обработчики кнопок панели (внешний вид собирает content.build_voice_panel_payload)."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TrustSelect())
        self.add_item(KickSelect())
        self.add_item(BanSelect())
        self.add_item(OwnerSelect())

    @discord.ui.button(label="Название", custom_id="vc:name", row=0)
    async def name(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = await get_room(interaction)
        if not room:
            return
        channel = interaction.guild.get_channel(room["channel_id"])
        settings = db.get_voice_settings(interaction.guild_id) or {}
        prefix = voice_prefix(settings.get("name_template") or DEFAULT_TEMPLATE)
        await interaction.response.send_modal(NameModal(channel.name, prefix))

    @discord.ui.button(label="Лимит", custom_id="vc:limit", row=0)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = await get_room(interaction)
        if not room:
            return
        channel = interaction.guild.get_channel(room["channel_id"])
        await interaction.response.send_modal(LimitModal(channel.user_limit))

    @discord.ui.button(label="Доступ", custom_id="vc:access", row=0)
    async def access(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = await get_room(interaction)
        if not room:
            return
        guild = interaction.guild
        channel = guild.get_channel(room["channel_id"])
        new_state = voice_next_state(room["state"])
        view, connect = voice_everyone_perms(new_state)
        await interaction.response.defer(ephemeral=True)
        try:
            await channel.set_permissions(guild.default_role, view_channel=view, connect=connect, reason="Доступ к комнате")
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось изменить доступ: {e}", ephemeral=True)
            return
        db.update_voice_room(room["channel_id"], state=new_state)
        db.set_voice_prefs(interaction.guild_id, room["owner_id"], state=new_state)
        await refresh_panel(interaction.client, guild, db.get_voice_room(room["channel_id"]))
        text = {"open": "Комната открыта для всех.", "locked": "Комната закрыта: её видят, но войти могут только доверенные.",
                "private": "Комната приватная: её видят и заходят только доверенные."}[new_state]
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="Удалить", custom_id="vc:delete", style=discord.ButtonStyle.danger, row=0)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = await get_room(interaction)
        if not room:
            return
        await interaction.response.send_message(
            "Удалить комнату? Участников отключит от канала.", view=ConfirmDelete(room["channel_id"]), ephemeral=True
        )


# ---------- Сообщение-хаб в канале ----------
INFO_SECTIONS = [
    (None, "Зайди в канал создания, и бот сделает тебе комнату. Управлять ей можно кнопками в её чате. "
           "Когда все выйдут, комната удалится, а название и лимит бот запомнит на следующий раз."),
    ("Доступ", "Открыта: заходят все.\nЗакрыта: видно всем, заходят доверенные.\nСкрыта: видят только доверенные."),
]


class HubView(discord.ui.View):
    """Кнопки общего сообщения: информация, добавить музыку, функции комнаты."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Информация", custom_id="vh:info")
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = db.get_voice_settings(interaction.guild_id) or {}
        trigger = interaction.guild.get_channel(settings["trigger_channel_id"]) if settings.get("trigger_channel_id") else None
        extras = [("Канал создания", trigger.mention)] if trigger else []
        payload = build_button_payload(title="Как это работает", sections=INFO_SECTIONS, extra_blocks=extras)
        try:
            await respond_components(interaction.client, interaction, payload)
        except discord.HTTPException:
            logging.exception("Не удалось показать информацию о комнатах")
            if not interaction.response.is_done():
                await interaction.response.send_message("\n\n".join(((f"**{h}**\n" if h else "") + b for h, b in INFO_SECTIONS))[:1900], ephemeral=True)

    @discord.ui.button(label="Функции", custom_id="vh:funcs", style=discord.ButtonStyle.primary)
    async def funcs(self, interaction: discord.Interaction, button: discord.ui.Button):
        room = db.get_voice_room_by_owner(interaction.guild_id, interaction.user.id)
        channel = interaction.guild.get_channel(room["channel_id"]) if room else None
        if channel is None:
            await interaction.response.send_message(
                "У вас пока нет своей комнаты. Зайдите в канал создания комнат, затем нажмите «Функции».", ephemeral=True
            )
            return
        image = load_asset("functions.jpg")
        trusted, banned = room_counts(channel, room["owner_id"], interaction.guild.me.id)
        payload = build_voice_panel_payload(
            owner_id=room["owner_id"], name=channel.name, state=room["state"], limit=channel.user_limit,
            trusted=trusted, banned=banned, image_name="functions.jpg" if image else None, ephemeral=True,
        )
        try:
            await respond_components(interaction.client, interaction, payload, (image, "functions.jpg") if image else None)
        except discord.HTTPException:
            logging.exception("Не удалось показать функции комнаты")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Управление комнатой: {channel.mention}, панель в её чате.", ephemeral=True)


# ---------- Модуль ----------
class Voice(commands.Cog):
    setting = app_commands.Group(name="voice_setting", description="Настройки временных голосовых комнат", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PanelView())
        self.bot.add_view(HubView())
        self.bot.loop.create_task(self.reconcile())

    async def reconcile(self):
        """После перезапуска: убрать комнаты, которых уже нет или которые опустели."""
        await self.bot.wait_until_ready()
        for room in db.list_voice_rooms():
            guild = self.bot.get_guild(room["guild_id"])
            channel = guild.get_channel(room["channel_id"]) if guild else None
            if channel is None:
                db.delete_voice_room(room["channel_id"])
            elif not channel.members:
                await delete_room(self.bot, guild, room["channel_id"])

    # ----- Создание и удаление комнат -----
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        try:
            settings = db.get_voice_settings(member.guild.id)
            if settings and after.channel and after.channel.id == settings["trigger_channel_id"]:
                await self.create_room(member, after.channel, settings)
            if before.channel and (after.channel is None or after.channel.id != before.channel.id):
                room = db.get_voice_room(before.channel.id)
                if room and not [m for m in before.channel.members if not m.bot]:
                    self.bot.loop.create_task(self.delete_if_empty(before.channel))
                elif room and room["owner_id"] == member.id:
                    await self.pass_ownership(before.channel, room)
        except Exception:
            logging.exception("Ошибка обработки голосового канала")

    async def pass_ownership(self, channel: discord.VoiceChannel, room: dict):
        """Владелец вышел, а в комнате остались люди: комната переходит к первому из оставшихся."""
        heirs = [m for m in channel.members if not m.bot]
        if not heirs:
            return
        new_owner = heirs[0]
        await transfer_owner(self.bot, channel.guild, channel, room, new_owner)
        try:
            await channel.send(f"{new_owner.mention} теперь владелец комнаты.",
                               allowed_mentions=discord.AllowedMentions.none(), delete_after=60)
        except discord.HTTPException:
            pass

    async def delete_if_empty(self, channel: discord.VoiceChannel):
        if channel.id in PENDING_DELETE:
            return
        PENDING_DELETE.add(channel.id)
        try:
            await asyncio.sleep(4)
            current = channel.guild.get_channel(channel.id)
            if current is not None and not [m for m in current.members if not m.bot] and db.get_voice_room(channel.id):
                await delete_room(self.bot, channel.guild, channel.id)
        finally:
            PENDING_DELETE.discard(channel.id)

    async def create_room(self, member: discord.Member, trigger: discord.VoiceChannel, settings: dict):
        guild = member.guild
        existing = db.get_voice_room_by_owner(guild.id, member.id)
        if existing:
            channel = guild.get_channel(existing["channel_id"])
            if channel:
                try:
                    await member.move_to(channel)
                except discord.HTTPException:
                    pass
                return
            db.delete_voice_room(existing["channel_id"])
        now = time.time()
        if now - LAST_CREATE.get(member.id, 0) < CREATE_COOLDOWN:
            return
        LAST_CREATE[member.id] = now
        prefs = db.get_voice_prefs(guild.id, member.id) or {}
        template = settings.get("name_template") or DEFAULT_TEMPLATE
        name = (prefs.get("name") or template.replace("{owner}", member.display_name))[:100]
        state = prefs.get("state") or "open"
        limit = prefs.get("user_limit") or 0
        category = guild.get_channel(settings["category_id"]) if settings.get("category_id") else trigger.category
        try:
            channel = await guild.create_voice_channel(
                name=name, category=category, overwrites=room_overwrites(guild, member, state),
                user_limit=limit, reason=f"Временная комната {member}",
            )
        except discord.HTTPException:
            logging.exception("Не удалось создать комнату")
            return
        db.create_voice_room(channel.id, guild.id, member.id, state, int(now))
        try:
            await member.move_to(channel)
        except discord.HTTPException:
            await delete_room(self.bot, guild, channel.id)
            return
        trusted, banned = room_counts(channel, member.id, guild.me.id)
        try:
            panel = await post_components(self.bot, channel, build_voice_panel_payload(
                owner_id=member.id, name=channel.name, state=state, limit=channel.user_limit, trusted=trusted, banned=banned,
            ))
            db.update_voice_room(channel.id, panel_message_id=int(panel["id"]))
        except discord.HTTPException:
            logging.warning("Не удалось отправить панель в комнату %s", channel.id)

    # ----- Команды -----
    @app_commands.command(name="voice", description="Как пользоваться временными голосовыми комнатами")
    @app_commands.guild_only()
    async def voice(self, interaction: discord.Interaction):
        settings = db.get_voice_settings(interaction.guild_id) or {}
        trigger = interaction.guild.get_channel(settings["trigger_channel_id"]) if settings.get("trigger_channel_id") else None
        steps = ("Зайди в " + (f"«{trigger.name}»" if trigger else "канал создания") + ", и бот сделает тебе комнату. "
                 "Управлять ей можно кнопками в её чате, а когда все выйдут, она удалится.")
        access = "Открыта: заходят все.\nЗакрыта: видно всем, заходят доверенные.\nСкрыта: видят только доверенные."
        extras = []
        room = db.get_voice_room_by_owner(interaction.guild_id, interaction.user.id)
        if room and interaction.guild.get_channel(room["channel_id"]):
            extras.append(("Ваша комната", f"<#{room['channel_id']}>"))
        payload = build_button_payload(title="Голосовые комнаты", emoji="voice", sections=[(None, steps), ("Доступ", access)], extra_blocks=extras)
        try:
            await respond_components(interaction.client, interaction, payload)
        except discord.HTTPException:
            logging.exception("Не удалось показать справку по комнатам")
            if not interaction.response.is_done():
                await interaction.response.send_message(steps + "\n\n" + access, ephemeral=True)

    @setting.command(name="setup", description="Настроить комнаты: канал создания, категория, шаблон названия")
    @app_commands.describe(
        create_channel="Голосовой канал: зайдя в него, участник получает свою комнату",
        category="Категория для комнат (по умолчанию та же, что у канала создания)",
        name_template="Название по умолчанию. {owner} заменится на ник владельца",
    )
    async def voice_setup(
        self,
        interaction: discord.Interaction,
        create_channel: discord.VoiceChannel,
        category: Optional[discord.CategoryChannel] = None,
        name_template: Optional[str] = None,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        target = category or create_channel.category
        perms = (target or create_channel).permissions_for(interaction.guild.me)
        if not (perms.manage_channels and perms.move_members and perms.view_channel):
            await interaction.response.send_message(
                "У бота должны быть права «Управление каналами», «Перемещать участников» и «Просмотр канала» "
                "в этой категории.", ephemeral=True,
            )
            return
        fields = {"trigger_channel_id": create_channel.id, "category_id": category.id if category else None}
        if name_template:
            fields["name_template"] = name_template.strip()[:80]
        db.set_voice_settings(interaction.guild_id, **fields)
        settings = db.get_voice_settings(interaction.guild_id)
        await interaction.response.send_message(
            f"Комнаты настроены.\n• Канал создания: {create_channel.mention}\n"
            f"• Категория: {target.mention if target else 'без категории'}\n"
            f"• Название по умолчанию: `{settings['name_template'] or DEFAULT_TEMPLATE}`",
            ephemeral=True,
        )

    @setting.command(name="hub", description="Отправить в канал сообщение с кнопками: информация, музыка, функции")
    @app_commands.describe(channel="Канал для сообщения (по умолчанию текущий)")
    async def voice_hub(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            await post_components(self.bot, target, build_hub_payload())
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить сообщение в {target.mention}: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"Сообщение отправлено в {target.mention}.", ephemeral=True)

    @setting.command(name="status", description="Показать настройки комнат и сколько их сейчас")
    async def voice_status(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        settings = db.get_voice_settings(interaction.guild_id)
        if not settings:
            await interaction.response.send_message("Комнаты ещё не настроены: /setting voice setup.", ephemeral=True)
            return
        rooms = [r for r in db.list_voice_rooms() if r["guild_id"] == interaction.guild_id]
        trigger = interaction.guild.get_channel(settings["trigger_channel_id"]) if settings["trigger_channel_id"] else None
        await interaction.response.send_message(
            f"Канал создания: {trigger.mention if trigger else 'не найден'}\n"
            f"Название: `{settings['name_template'] or DEFAULT_TEMPLATE}`\nКомнат сейчас: {len(rooms)}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Voice(bot))
