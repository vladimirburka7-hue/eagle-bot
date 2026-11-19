"""Стаки: команды игроков внутри сервера (роли, каналы, заявки, управление капитаном)."""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from content import (
    build_stack_control_payload, build_stack_list_payload, build_stack_panel_payload,
    build_stacks_main_payload, edit_components, post_components,
)
from emojis import EMOJI
from utils import (
    can_manage, clean_stack_name, stack_color, stack_text_channel_name,
    stack_voice_channel_name, valid_stack_name,
)

NO_PING = discord.AllowedMentions.none()
RENAME_COOLDOWN = 7 * 24 * 3600  # капитан может переименовать стак раз в 7 дней


class StackError(Exception):
    pass


# ---------- Вспомогательное ----------
def can_control(member: discord.Member, stack: dict) -> bool:
    return member.id == stack["captain_id"] or can_manage(member)


def member_label(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)
    return member.display_name if member else f"ID {user_id}"


async def guard(interaction: discord.Interaction) -> Optional[dict]:
    """Стак по каналу и проверка, что нажал капитан или персонал."""
    stack = db.get_stack_by_channel(interaction.channel_id)
    if not stack:
        await interaction.response.send_message("Это не канал стака.", ephemeral=True)
        return None
    if not can_control(interaction.user, stack):
        await interaction.response.send_message("Только капитан стака или персонал.", ephemeral=True)
        return None
    return stack


async def dm(member: discord.abc.User, text: str):
    try:
        await member.send(text)
    except discord.HTTPException:
        pass  # закрыты личные сообщения


def stack_overwrites(guild, role, captain_role):
    """Права для текстового и голосового каналов стака."""
    PO = discord.PermissionOverwrite
    staff = [r for r in (guild.get_role(i) for i in db.get_staff_role_ids(guild.id)) if r]
    text = {
        guild.default_role: PO(view_channel=False),
        guild.me: PO(view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                     read_message_history=True, manage_channels=True, manage_messages=True),
        role: PO(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        captain_role: PO(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True),
    }
    voice = {
        guild.default_role: PO(view_channel=False, connect=False),
        guild.me: PO(view_channel=True, connect=True, move_members=True, manage_channels=True),
        role: PO(view_channel=True, connect=True, speak=True),
        captain_role: PO(view_channel=True, connect=True, speak=True, move_members=True),
    }
    for r in staff:
        text[r] = PO(view_channel=True, send_messages=True, read_message_history=True)
        voice[r] = PO(view_channel=True, connect=True, speak=True)
    return text, voice


async def arrange_roles(guild: discord.Guild, role, captain_role, settings, force: bool = False):
    """Роль капитана выше роли стака; если задана роль-якорь, обе роли ставятся прямо под ней."""
    anchor = None
    if settings and settings.get("role_anchor_id"):
        anchor = guild.get_role(settings["role_anchor_id"])
    try:
        if anchor:
            await guild.edit_role_positions({
                captain_role: max(1, anchor.position - 1), role: max(1, anchor.position - 2),
            })
        elif force or captain_role.position <= role.position:
            await guild.edit_role_positions({captain_role: role.position + 1})
    except discord.HTTPException:
        logging.warning("Не удалось расставить роли стака (роль бота должна быть выше ролей стаков)")


# ---------- Обновление сообщений ----------
async def refresh_stack(bot, guild: discord.Guild, stack_id: int):
    """Перерисовывает панель заявок и панель капитана в канале стака."""
    stack = db.get_stack(stack_id)
    if not stack or not stack["text_channel_id"]:
        return
    settings = db.get_stack_settings(guild.id) or {}
    members = db.list_stack_members(stack_id)
    applicants = [(uid, member_label(guild, uid)) for uid, _ts in db.list_stack_apps(stack_id)]
    panel = build_stack_panel_payload(
        name=stack["name"], captain_id=stack["captain_id"], members=members,
        max_members=settings.get("max_members", 10), applicants=applicants,
        description=stack["description"] or "",
    )
    voice = guild.get_channel(stack["voice_channel_id"]) if stack["voice_channel_id"] else None
    guests_open = bool(voice and voice.overwrites_for(guild.default_role).connect is True)
    control = build_stack_control_payload(
        name=stack["name"], guests_open=guests_open, limit=voice.user_limit if voice else 0,
    )
    for message_id, payload in ((stack["panel_message_id"], panel), (stack["control_message_id"], control)):
        if message_id:
            try:
                await edit_components(bot, stack["text_channel_id"], message_id, payload)
            except discord.HTTPException:
                logging.warning("Не удалось обновить сообщение стака %s", stack_id)


async def refresh_list(bot, guild: discord.Guild):
    """Обновляет каталог стаков и сообщает памятке-обзору, что данные изменились."""
    try:
        await _refresh_list(bot, guild)
    finally:
        bot.dispatch("stack_update", guild)


async def _refresh_list(bot, guild: discord.Guild):
    settings = db.get_stack_settings(guild.id)
    if not settings or not settings["list_channel_id"]:
        return
    channel = guild.get_channel(settings["list_channel_id"])
    if channel is None:
        return
    stacks = [
        {"name": st["name"], "captain_id": st["captain_id"],
         "count": len(db.list_stack_members(st["id"])), "description": st["description"] or ""}
        for st in db.list_stacks(guild.id)
    ]
    payload = build_stack_list_payload(stacks, settings["max_members"])
    if settings["list_message_id"]:
        try:
            await edit_components(bot, channel.id, settings["list_message_id"], payload)
            return
        except discord.HTTPException:
            pass
    try:
        message = await post_components(bot, channel, payload)
        db.set_stack_settings(guild.id, list_message_id=int(message["id"]))
    except discord.HTTPException:
        logging.warning("Не удалось обновить каталог стаков")


# ---------- Создание и удаление ----------
def precheck_create(guild, user, name: Optional[str] = None) -> Optional[str]:
    settings = db.get_stack_settings(guild.id)
    if not settings or not settings["category_id"]:
        return "Стаки ещё не настроены. Сообщите администрации (/setting stack setup)."
    if db.get_member_stack(guild.id, user.id):
        return "Вы уже состоите в стаке."
    if db.find_pending_stack_request(guild.id, user.id):
        return "Ваша заявка на создание стака уже на рассмотрении."
    if len(db.list_stacks(guild.id)) >= settings["max_stacks"]:
        return "Достигнут лимит стаков на сервере."
    if name is not None:
        if not valid_stack_name(name):
            return "Название: 2–24 символа, буквы, цифры, пробел, точка, дефис или подчёркивание."
        if db.find_stack_by_name(guild.id, name):
            return "Стак с таким названием уже есть."
    return None


async def build_stack(bot, guild: discord.Guild, name: str, description: str, captain: discord.Member) -> dict:
    settings = db.get_stack_settings(guild.id)
    category = guild.get_channel(settings["category_id"]) if settings else None
    if not isinstance(category, discord.CategoryChannel):
        raise StackError("Категория для стаков не настроена (/setting stack setup).")
    created = []
    try:
        colour = discord.Colour(stack_color(name))
        # сначала роль капитана, потом роль стака: новая роль встаёт ниже, значит капитанская окажется выше
        captain_role = await guild.create_role(
            name=f"・ The captain of {name}"[:100], colour=colour, reason=f"Капитан стака {name}"
        )
        created.append(captain_role)
        role = await guild.create_role(name=f"・ {name}"[:100], colour=colour, reason=f"Стак {name}")
        created.append(role)
        await arrange_roles(guild, role, captain_role, settings)
        text_ow, voice_ow = stack_overwrites(guild, role, captain_role)
        text = await guild.create_text_channel(
            name=stack_text_channel_name(name), category=category, overwrites=text_ow,
            topic=f"Стак {name}. Капитан: {captain}", reason=f"Стак {name}",
        )
        created.append(text)
        voice = await guild.create_voice_channel(
            name=stack_voice_channel_name(name), category=category, overwrites=voice_ow,
            reason=f"Стак {name}",
        )
        created.append(voice)
        await captain.add_roles(role, captain_role, reason=f"Создан стак {name}")
    except discord.HTTPException as e:
        for obj in reversed(created):
            try:
                await obj.delete(reason="Откат создания стака")
            except discord.HTTPException:
                pass
        raise StackError(
            f"Не удалось создать стак: {e}. Проверьте, что у бота есть права на роли и каналы "
            f"и что его роль выше создаваемых."
        )

    now = int(time.time())
    stack_id = db.create_stack(guild.id, name, description or "", captain.id, now)
    db.update_stack(stack_id, role_id=role.id, captain_role_id=captain_role.id,
                    text_channel_id=text.id, voice_channel_id=voice.id)
    db.add_stack_member(stack_id, captain.id, now)

    stack = db.get_stack(stack_id)
    members = [captain.id]
    try:
        panel = await post_components(bot, text, build_stack_panel_payload(
            name=name, captain_id=captain.id, members=members,
            max_members=settings["max_members"], applicants=[], description=description or "",
        ))
        control = await post_components(bot, text, build_stack_control_payload(
            name=name, guests_open=False, limit=0,
        ))
        db.update_stack(stack_id, panel_message_id=int(panel["id"]), control_message_id=int(control["id"]))
    except discord.HTTPException:
        logging.exception("Не удалось отправить панели стака")
    await refresh_list(bot, guild)
    return db.get_stack(stack_id) or stack


async def disband_stack(bot, guild: discord.Guild, stack: dict):
    for key in ("voice_channel_id", "text_channel_id"):
        channel = guild.get_channel(stack[key]) if stack[key] else None
        if channel:
            try:
                await channel.delete(reason=f"Стак {stack['name']} распущен")
            except discord.HTTPException:
                logging.warning("Не удалось удалить канал стака")
    for key in ("role_id", "captain_role_id"):
        role = guild.get_role(stack[key]) if stack[key] else None
        if role:
            try:
                await role.delete(reason=f"Стак {stack['name']} распущен")
            except discord.HTTPException:
                logging.warning("Не удалось удалить роль стака")
    db.delete_stack(stack["id"])
    await refresh_list(bot, guild)


# ---------- Заявки в стак ----------
async def apply_to_stack(bot, guild: discord.Guild, user: discord.Member, stack_id: int) -> str:
    stack = db.get_stack(stack_id)
    settings = db.get_stack_settings(guild.id) or {}
    if not stack:
        return "Стак не найден."
    if db.get_member_stack(guild.id, user.id):
        return "Вы уже состоите в стаке."
    if db.get_stack_app(stack_id, user.id):
        return "Заявка в этот стак уже отправлена."
    if len(db.list_stack_members(stack_id)) >= settings.get("max_members", 10):
        return "Стак заполнен."
    db.add_stack_app(stack_id, user.id, int(time.time()))
    await refresh_stack(bot, guild, stack_id)
    channel = guild.get_channel(stack["text_channel_id"]) if stack["text_channel_id"] else None
    if channel:
        try:
            await channel.send(
                f"<@{stack['captain_id']}>, новая заявка в стак: {user.mention}",
                allowed_mentions=discord.AllowedMentions(users=[discord.Object(stack["captain_id"])]),
                delete_after=180,
            )
        except discord.HTTPException:
            pass
    return f"Заявка в стак «{stack['name']}» отправлена. Капитан рассмотрит её."


async def accept_member(bot, guild: discord.Guild, stack: dict, user_id: int) -> str:
    settings = db.get_stack_settings(guild.id) or {}
    if not db.get_stack_app(stack["id"], user_id):
        return "Заявка уже обработана."
    member = guild.get_member(user_id)
    if member is None:
        db.remove_stack_app(stack["id"], user_id)
        await refresh_stack(bot, guild, stack["id"])
        return "Участника уже нет на сервере."
    if db.get_member_stack(guild.id, user_id):
        db.remove_stack_app(stack["id"], user_id)
        await refresh_stack(bot, guild, stack["id"])
        return "Участник уже состоит в другом стаке."
    if len(db.list_stack_members(stack["id"])) >= settings.get("max_members", 10):
        return "Стак заполнен."
    role = guild.get_role(stack["role_id"]) if stack["role_id"] else None
    try:
        if role:
            await member.add_roles(role, reason=f"Принят в стак {stack['name']}")
    except discord.HTTPException:
        return "Не удалось выдать роль: роль бота должна стоять выше роли стака."
    db.add_stack_member(stack["id"], user_id, int(time.time()))
    others = set(db.remove_user_apps(guild.id, user_id))
    for other_id in others - {stack["id"]}:
        await refresh_stack(bot, guild, other_id)
    await refresh_stack(bot, guild, stack["id"])
    await refresh_list(bot, guild)
    await dm(member, f"Вас приняли в стак «{stack['name']}» на сервере **{guild.name}**.")
    return f"{member.mention} добавлен(а) в стак."


async def decline_member(bot, guild: discord.Guild, stack: dict, user_id: int) -> str:
    if not db.get_stack_app(stack["id"], user_id):
        return "Заявка уже обработана."
    db.remove_stack_app(stack["id"], user_id)
    await refresh_stack(bot, guild, stack["id"])
    member = guild.get_member(user_id)
    if member:
        await dm(member, f"Заявка в стак «{stack['name']}» на сервере **{guild.name}** отклонена.")
    return "Заявка отклонена."


async def remove_member(bot, guild: discord.Guild, stack: dict, member: discord.Member) -> str:
    if member.id == stack["captain_id"]:
        return "Капитана исключить нельзя: передайте роль капитана или распустите стак."
    if member.id not in db.list_stack_members(stack["id"]):
        return "Этот участник не состоит в стаке."
    roles = [r for r in (guild.get_role(stack["role_id"]),) if r and r in member.roles]
    try:
        if roles:
            await member.remove_roles(*roles, reason=f"Исключён из стака {stack['name']}")
    except discord.HTTPException:
        return "Не удалось снять роль: роль бота должна стоять выше роли стака."
    db.remove_stack_member(stack["id"], member.id)
    voice = guild.get_channel(stack["voice_channel_id"]) if stack["voice_channel_id"] else None
    if voice and member.voice and member.voice.channel and member.voice.channel.id == voice.id:
        try:
            await member.move_to(None)
        except discord.HTTPException:
            pass
    await refresh_stack(bot, guild, stack["id"])
    await refresh_list(bot, guild)
    await dm(member, f"Вы исключены из стака «{stack['name']}» на сервере **{guild.name}**.")
    return f"{member.mention} исключён(а) из стака."


# ---------- Формы ----------
class CreateModal(discord.ui.Modal, title="Создать стак"):
    def __init__(self):
        super().__init__(timeout=900)
        self.name = discord.ui.TextInput(
            label="Название стака", min_length=2, max_length=24, placeholder="Например: Evolution",
        )
        self.description = discord.ui.TextInput(
            label="Описание (необязательно)", style=discord.TextStyle.paragraph,
            required=False, max_length=200, placeholder="Чем занимается стак, когда играет",
        )
        self.add_item(self.name)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        name = clean_stack_name(self.name.value)
        description = self.description.value.strip()
        error = precheck_create(guild, user, name)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        settings = db.get_stack_settings(guild.id)
        if settings["auto_approve"]:
            try:
                stack = await build_stack(interaction.client, guild, name, description, user)
            except StackError as e:
                await interaction.followup.send(str(e), ephemeral=True)
                return
            channel = guild.get_channel(stack["text_channel_id"])
            await interaction.followup.send(
                f"Стак «{name}» создан: {channel.mention if channel else ''}", ephemeral=True
            )
            return
        review = guild.get_channel(settings["review_channel_id"]) if settings["review_channel_id"] else None
        if review is None:
            await interaction.followup.send("Канал для заявок на стаки не настроен.", ephemeral=True)
            return
        embed = discord.Embed(title="Заявка на стак", color=0x5865F2)
        embed.add_field(name="Статус", value=f"{EMOJI['wait']} Ждёт ответа", inline=False)
        embed.add_field(name="Название", value=name, inline=False)
        embed.add_field(name="Капитан", value=user.mention, inline=False)
        if description:
            embed.add_field(name="Описание", value=description, inline=False)
        embed.set_thumbnail(url=user.display_avatar.url)
        try:
            message = await review.send(embed=embed, view=StackRequestView(), allowed_mentions=NO_PING)
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить заявку: {e}", ephemeral=True)
            return
        db.create_stack_request(message.id, review.id, guild.id, user.id, name, description)
        await interaction.followup.send(
            f"{EMOJI['tick']} Готово, заявка на стак отправлена. Ответ придёт в личку.", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы стака", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class LimitModal(discord.ui.Modal, title="Лимит голосового канала"):
    def __init__(self, stack_id: int, current: int):
        super().__init__()
        self.stack_id = stack_id
        self.limit = discord.ui.TextInput(
            label="Сколько человек (0 = без лимита)", max_length=2, default=str(current or 0),
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction):
        stack = db.get_stack(self.stack_id)
        if not stack or not can_control(interaction.user, stack):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        try:
            number = int(self.limit.value.strip())
            if not 0 <= number <= 99:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Нужно число от 0 до 99.", ephemeral=True)
            return
        voice = interaction.guild.get_channel(stack["voice_channel_id"])
        if voice is None:
            await interaction.response.send_message("Голосовой канал не найден.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await voice.edit(user_limit=number, reason="Лимит стака")
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось изменить лимит: {e}", ephemeral=True)
            return
        await refresh_stack(interaction.client, interaction.guild, stack["id"])
        await interaction.followup.send(f"Лимит: {number or 'без лимита'}.", ephemeral=True)


class RenameModal(discord.ui.Modal, title="Переименовать стак"):
    def __init__(self, stack_id: int, current: str):
        super().__init__()
        self.stack_id = stack_id
        self.name = discord.ui.TextInput(label="Новое название", min_length=2, max_length=24, default=current)
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        stack = db.get_stack(self.stack_id)
        if not stack or not can_control(interaction.user, stack):
            await interaction.response.send_message("Нет прав.", ephemeral=True)
            return
        name = clean_stack_name(self.name.value)
        if name == stack["name"]:
            await interaction.response.send_message("Название не изменилось.", ephemeral=True)
            return
        if not valid_stack_name(name):
            await interaction.response.send_message(
                "Название: 2–24 символа, буквы, цифры, пробел, точка, дефис или подчёркивание.", ephemeral=True
            )
            return
        other = db.find_stack_by_name(guild.id, name)
        if other and other["id"] != stack["id"]:
            await interaction.response.send_message("Стак с таким названием уже есть.", ephemeral=True)
            return
        now = int(time.time())
        if not can_manage(interaction.user) and stack["renamed_at"] and now - stack["renamed_at"] < RENAME_COOLDOWN:
            days = (RENAME_COOLDOWN - (now - stack["renamed_at"])) // 86400 + 1
            await interaction.response.send_message(
                f"Переименовать можно раз в 7 дней. Подождите ещё около {days} дн.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        failed = []
        jobs = [
            (guild.get_role(stack["role_id"]), {"name": f"・ {name}"[:100]}),
            (guild.get_role(stack["captain_role_id"]), {"name": f"・ The captain of {name}"[:100]}),
            (guild.get_channel(stack["text_channel_id"]), {"name": stack_text_channel_name(name)}),
            (guild.get_channel(stack["voice_channel_id"]), {"name": stack_voice_channel_name(name)}),
        ]
        for obj, fields in jobs:
            if obj is None:
                continue
            try:
                await obj.edit(reason="Стак переименован", **fields)
            except discord.HTTPException:
                failed.append(obj.name)
        db.update_stack(stack["id"], name=name, renamed_at=now)
        await refresh_stack(interaction.client, guild, stack["id"])
        await refresh_list(interaction.client, guild)
        note = f" Не удалось переименовать: {', '.join(failed)}." if failed else ""
        await interaction.followup.send(f"Стак переименован в «{name}».{note}", ephemeral=True)


# ---------- Панель заявок на создание (для персонала) ----------
class StackRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать стак", style=discord.ButtonStyle.success, custom_id="st:req:ok")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = db.get_stack_request(interaction.message.id)
        if not req or req["status"] != "pending":
            await interaction.response.send_message("Заявка уже обработана.", ephemeral=True)
            return
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = guild.get_member(req["user_id"])
        error = None
        if member is None:
            error = "Заявитель уже не на сервере."
        elif db.get_member_stack(guild.id, member.id):
            error = "Заявитель уже состоит в стаке."
        elif db.find_stack_by_name(guild.id, req["name"]):
            error = "Название уже занято."
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        try:
            stack = await build_stack(interaction.client, guild, req["name"], req["description"] or "", member)
        except StackError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        db.set_stack_request_status(req["message_id"], "approved")
        await self.finish(interaction, f"{EMOJI['ok']} Создан", 0x3BA55D)
        channel = guild.get_channel(stack["text_channel_id"])
        await dm(member, f"Ваш стак «{stack['name']}» создан на сервере **{guild.name}**."
                         + (f" Канал: {channel.mention}" if channel else ""))
        await interaction.followup.send(f"Стак создан: {channel.mention if channel else ''}", ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="st:req:no")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = db.get_stack_request(interaction.message.id)
        if not req or req["status"] != "pending":
            await interaction.response.send_message("Заявка уже обработана.", ephemeral=True)
            return
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        db.set_stack_request_status(req["message_id"], "declined")
        await self.finish(interaction, f"{EMOJI['no']} Отклонён", 0xED4245)
        member = interaction.guild.get_member(req["user_id"])
        if member:
            await dm(member, f"Заявка на создание стака «{req['name']}» на сервере "
                             f"**{interaction.guild.name}** отклонена.")
        await interaction.followup.send("Заявка отклонена.", ephemeral=True)

    @staticmethod
    async def finish(interaction: discord.Interaction, label: str, color: int):
        message = interaction.message
        try:
            embed = message.embeds[0].copy() if message.embeds else discord.Embed()
            embed.color = color
            status = f"{label}, {interaction.user.mention}"
            for i, field in enumerate(embed.fields):
                if field.name == "Статус":
                    embed.set_field_at(i, name="Статус", value=status, inline=False)
                    break
            else:
                embed.add_field(name="Статус", value=status, inline=False)
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass


def my_stack_text(guild: discord.Guild, user: discord.Member) -> str:
    stack = db.get_member_stack(guild.id, user.id)
    if not stack:
        return "Вы не состоите в стаке. Создайте свой или вступите: /stack join."
    channel = guild.get_channel(stack["text_channel_id"]) if stack["text_channel_id"] else None
    members = db.list_stack_members(stack["id"])
    return (f"**{stack['name']}**\nКапитан: <@{stack['captain_id']}>\nСостав: {len(members)}\n"
            + (f"Канал: {channel.mention}" if channel else ""))


# ---------- Главная панель ----------
class StackPick(discord.ui.Select):
    def __init__(self, guild: discord.Guild, stacks: list, max_members: int):
        options = []
        for st in stacks[:25]:
            count = len(db.list_stack_members(st["id"]))
            options.append(discord.SelectOption(
                label=st["name"][:100], value=str(st["id"]),
                description=f"{count}/{max_members} · капитан {member_label(guild, st['captain_id'])}"[:100],
            ))
        super().__init__(placeholder="Выберите стак", options=options)

    async def callback(self, interaction: discord.Interaction):
        text = await apply_to_stack(interaction.client, interaction.guild, interaction.user, int(self.values[0]))
        await interaction.response.edit_message(content=text, view=None)


class StackPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать стак", custom_id="st:create")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        error = precheck_create(interaction.guild, interaction.user)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(CreateModal())

    @discord.ui.button(label="Вступить в стак", custom_id="st:join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        stacks = db.list_stacks(guild.id)
        if not stacks:
            await interaction.response.send_message("Пока нет ни одного стака.", ephemeral=True)
            return
        if db.get_member_stack(guild.id, interaction.user.id):
            await interaction.response.send_message("Вы уже состоите в стаке.", ephemeral=True)
            return
        settings = db.get_stack_settings(guild.id) or {}
        view = discord.ui.View(timeout=180)
        view.add_item(StackPick(guild, stacks, settings.get("max_members", 10)))
        extra = "\nПоказаны первые 25, остальные: /stack join." if len(stacks) > 25 else ""
        await interaction.response.send_message("В какой стак подать заявку?" + extra, view=view, ephemeral=True)

    @discord.ui.button(label="Мой стак", custom_id="st:me")
    async def me(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            my_stack_text(interaction.guild, interaction.user), ephemeral=True, allowed_mentions=NO_PING
        )

    @discord.ui.button(label="Выйти из стака", custom_id="st:leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = db.get_member_stack(interaction.guild_id, interaction.user.id)
        if not stack:
            await interaction.response.send_message("Вы не состоите в стаке.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await remove_member(interaction.client, interaction.guild, stack, interaction.user)
        await interaction.followup.send(text.replace("исключён(а) из стака", "вышли из стака"), ephemeral=True, allowed_mentions=NO_PING)


# ---------- Панель заявок в канале стака ----------
class AcceptSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="st:accept", placeholder="Добавить в стак",
                         options=[discord.SelectOption(label="Нет заявок", value="0")])

    async def callback(self, interaction: discord.Interaction):
        stack = await guard(interaction)
        if not stack:
            return
        user_id = int(self.values[0])
        if user_id == 0:
            await interaction.response.send_message("Заявок нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await accept_member(interaction.client, interaction.guild, stack, user_id)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PING)


class DeclineSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="st:decline", placeholder="Отклонить заявку",
                         options=[discord.SelectOption(label="Нет заявок", value="0")])

    async def callback(self, interaction: discord.Interaction):
        stack = await guard(interaction)
        if not stack:
            return
        user_id = int(self.values[0])
        if user_id == 0:
            await interaction.response.send_message("Заявок нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await decline_member(interaction.client, interaction.guild, stack, user_id)
        await interaction.followup.send(text, ephemeral=True)


class ApplicantsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AcceptSelect())
        self.add_item(DeclineSelect())


# ---------- Панель капитана ----------
class PermitSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="st:permit", placeholder="Пустить в войс (гость)", row=1)

    async def callback(self, interaction: discord.Interaction):
        stack = await guard(interaction)
        if not stack:
            return
        guild = interaction.guild
        target = guild.get_member(self.values[0].id)
        voice = guild.get_channel(stack["voice_channel_id"]) if stack["voice_channel_id"] else None
        if target is None or voice is None:
            await interaction.response.send_message("Участник или голосовой канал не найден.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await voice.set_permissions(target, view_channel=True, connect=True, reason="Гость в войсе стака")
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось выдать доступ: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"{target.mention} может зайти в голосовой канал.", ephemeral=True, allowed_mentions=NO_PING)


class RemoveSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="st:remove", placeholder="Исключить из стака", row=2)

    async def callback(self, interaction: discord.Interaction):
        stack = await guard(interaction)
        if not stack:
            return
        target = interaction.guild.get_member(self.values[0].id)
        if target is None:
            await interaction.response.send_message("Участника нет на сервере.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await remove_member(interaction.client, interaction.guild, stack, target)
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PING)


class TransferSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(custom_id="st:transfer", placeholder="Передать капитана", row=3)

    async def callback(self, interaction: discord.Interaction):
        stack = await guard(interaction)
        if not stack:
            return
        guild = interaction.guild
        target = guild.get_member(self.values[0].id)
        if target is None or target.id not in db.list_stack_members(stack["id"]):
            await interaction.response.send_message("Капитаном можно назначить только участника стака.", ephemeral=True)
            return
        if target.id == stack["captain_id"]:
            await interaction.response.send_message("Этот участник уже капитан.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cap_role = guild.get_role(stack["captain_role_id"]) if stack["captain_role_id"] else None
        old = guild.get_member(stack["captain_id"])
        try:
            if cap_role:
                if old and cap_role in old.roles:
                    await old.remove_roles(cap_role, reason="Передача капитана")
                await target.add_roles(cap_role, reason="Передача капитана")
        except discord.HTTPException:
            await interaction.followup.send("Не удалось поменять роли: роль бота должна стоять выше ролей стака.", ephemeral=True)
            return
        db.update_stack(stack["id"], captain_id=target.id)
        await refresh_stack(interaction.client, guild, stack["id"])
        await refresh_list(interaction.client, guild)
        await dm(target, f"Вы назначены капитаном стака «{stack['name']}» на сервере **{guild.name}**.")
        await interaction.followup.send(f"Новый капитан: {target.mention}", ephemeral=True, allowed_mentions=NO_PING)


class ConfirmDisband(discord.ui.View):
    def __init__(self, stack_id: int):
        super().__init__(timeout=60)
        self.stack_id = stack_id

    @discord.ui.button(label="Да, распустить стак", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = db.get_stack(self.stack_id)
        if not stack or not can_control(interaction.user, stack):
            await interaction.response.edit_message(content="Нет прав или стак уже удалён.", view=None)
            return
        await interaction.response.edit_message(content="Распускаю стак…", view=None)
        await disband_stack(interaction.client, interaction.guild, stack)


class ControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PermitSelect())
        self.add_item(RemoveSelect())
        self.add_item(TransferSelect())

    @discord.ui.button(label="Лимит войса", custom_id="st:limit", row=0)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = await guard(interaction)
        if not stack:
            return
        voice = interaction.guild.get_channel(stack["voice_channel_id"]) if stack["voice_channel_id"] else None
        await interaction.response.send_modal(LimitModal(stack["id"], voice.user_limit if voice else 0))

    @discord.ui.button(label="Открыть для гостей", custom_id="st:guests", row=0)
    async def guests(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = await guard(interaction)
        if not stack:
            return
        guild = interaction.guild
        voice = guild.get_channel(stack["voice_channel_id"]) if stack["voice_channel_id"] else None
        if voice is None:
            await interaction.response.send_message("Голосовой канал не найден.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        opened = voice.overwrites_for(guild.default_role).connect is True
        try:
            await voice.set_permissions(
                guild.default_role, view_channel=not opened, connect=not opened, reason="Доступ гостей в войс стака"
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось изменить доступ: {e}", ephemeral=True)
            return
        await refresh_stack(interaction.client, guild, stack["id"])
        await interaction.followup.send(
            "Войс закрыт: заходят только участники стака." if opened else "Войс открыт для всех.", ephemeral=True
        )

    @discord.ui.button(label="Переименовать", style=discord.ButtonStyle.primary, custom_id="st:rename", row=0)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = await guard(interaction)
        if not stack:
            return
        await interaction.response.send_modal(RenameModal(stack["id"], stack["name"]))

    @discord.ui.button(label="Распустить", style=discord.ButtonStyle.danger, custom_id="st:disband", row=0)
    async def disband(self, interaction: discord.Interaction, button: discord.ui.Button):
        stack = await guard(interaction)
        if not stack:
            return
        await interaction.response.send_message(
            f"Распустить стак «{stack['name']}»? Роли и каналы будут удалены.",
            view=ConfirmDisband(stack["id"]), ephemeral=True,
        )


# ---------- Команды ----------
async def stack_autocomplete(interaction: discord.Interaction, current: str):
    current = current.casefold()
    return [
        app_commands.Choice(name=st["name"][:100], value=st["name"][:100])
        for st in db.list_stacks(interaction.guild_id)
        if current in st["name"].casefold()
    ][:25]


class Stacks(commands.Cog):
    stack_grp = app_commands.Group(name="stack", description="Стаки: вступить, выйти, свой стак", guild_only=True)
    setting = app_commands.Group(name="stack_setting", description="Настройки стаков", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(StackPanelView())
        self.bot.add_view(StackRequestView())
        self.bot.add_view(ApplicantsView())
        self.bot.add_view(ControlView())

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Если участник ушёл с сервера, убираем его из стака и его заявок."""
        guild = member.guild
        stack = db.get_member_stack(guild.id, member.id)
        for stack_id in db.remove_user_apps(guild.id, member.id):
            await refresh_stack(self.bot, guild, stack_id)
        if not stack:
            return
        db.remove_stack_member(stack["id"], member.id)
        await refresh_stack(self.bot, guild, stack["id"])
        await refresh_list(self.bot, guild)
        if stack["captain_id"] == member.id:
            channel = guild.get_channel(stack["text_channel_id"]) if stack["text_channel_id"] else None
            if channel:
                try:
                    await channel.send(
                        f"Капитан {member} покинул сервер. Персонал может назначить нового капитана "
                        f"через панель управления или распустить стак."
                    )
                except discord.HTTPException:
                    pass

    @setting.command(name="setup", description="Настроить стаки: категория, каналы, лимиты")
    @app_commands.describe(
        category="Категория, где создаются каналы стаков",
        review_channel="Канал персонала для заявок на создание стака",
        list_channel="Канал с каталогом стаков (необязательно)",
        max_members="Максимум участников в стаке",
        max_stacks="Максимум стаков на сервере",
        auto_approve="Создавать стаки сразу, без проверки персоналом",
        role_anchor="Роли стаков будут созданы прямо под этой ролью (необязательно)",
    )
    async def stacks_setup(
        self,
        interaction: discord.Interaction,
        category: discord.CategoryChannel,
        review_channel: discord.TextChannel,
        list_channel: Optional[discord.TextChannel] = None,
        max_members: app_commands.Range[int, 2, 30] = 10,
        max_stacks: app_commands.Range[int, 1, 100] = 20,
        auto_approve: bool = False,
        role_anchor: Optional[discord.Role] = None,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        me = interaction.guild.me
        if not me.guild_permissions.manage_roles or not category.permissions_for(me).manage_channels:
            await interaction.response.send_message(
                "У бота должны быть права «Управление ролями» и «Управление каналами» в этой категории.",
                ephemeral=True,
            )
            return
        db.set_stack_settings(
            interaction.guild_id, category_id=category.id, review_channel_id=review_channel.id,
            list_channel_id=list_channel.id if list_channel else None, list_message_id=None,
            max_members=max_members, max_stacks=max_stacks, auto_approve=int(auto_approve),
            role_anchor_id=role_anchor.id if role_anchor else None,
        )
        if list_channel:
            await refresh_list(self.bot, interaction.guild)
        await interaction.response.send_message(
            f"Стаки настроены.\n• Категория: {category.mention}\n• Заявки на создание: {review_channel.mention}\n"
            + (f"• Каталог: {list_channel.mention}\n" if list_channel else "")
            + f"• До {max_members} человек в стаке, до {max_stacks} стаков\n"
            + f"• Создание: {'сразу' if auto_approve else 'после проверки персоналом'}"
            + (f"\n• Роли стаков создаются под {role_anchor.mention}" if role_anchor else ""),
            ephemeral=True, allowed_mentions=NO_PING,
        )

    @setting.command(name="panel", description="Отправить панель стаков")
    @app_commands.describe(channel="Канал для панели (по умолчанию текущий)")
    async def stack_panel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            await post_components(self.bot, target, build_stacks_main_payload())
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить панель: {e}", ephemeral=True)
            return
        note = "" if db.get_stack_settings(interaction.guild_id) else "\nСтаки ещё не настроены: /setting stack setup."
        await interaction.followup.send(f"Панель отправлена в {target.mention}.{note}", ephemeral=True)

    @setting.command(name="fix", description="Привести роли существующих стаков к единому виду")
    async def stacks_fix(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = db.get_stack_settings(guild.id)
        fixed, problems = 0, []
        for st in db.list_stacks(guild.id):
            role = guild.get_role(st["role_id"]) if st["role_id"] else None
            cap = guild.get_role(st["captain_role_id"]) if st["captain_role_id"] else None
            if role is None or cap is None:
                problems.append(st["name"])
                continue
            want = f"・ The captain of {st['name']}"[:100]
            try:
                if cap.name != want:
                    await cap.edit(name=want, reason="Стак: единый вид ролей")
                await arrange_roles(guild, role, cap, settings, force=cap.position <= role.position)
                fixed += 1
            except discord.HTTPException:
                problems.append(st["name"])
            await refresh_stack(interaction.client, guild, st["id"])
        await refresh_list(interaction.client, guild)
        text = f"Обработано стаков: {fixed}."
        if problems:
            text += "\nНе удалось: " + ", ".join(problems) + " (роль бота должна быть выше ролей стаков)."
        await interaction.followup.send(text, ephemeral=True)

    @stack_grp.command(name="me", description="Показать мой стак")
    async def stack_me(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            my_stack_text(interaction.guild, interaction.user), ephemeral=True, allowed_mentions=NO_PING
        )

    @stack_grp.command(name="join", description="Подать заявку в стак")
    @app_commands.describe(name="Название стака")
    @app_commands.autocomplete(name=stack_autocomplete)
    async def stack_join(self, interaction: discord.Interaction, name: str):
        stack = db.find_stack_by_name(interaction.guild_id, name)
        if not stack:
            await interaction.response.send_message("Такого стака нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await apply_to_stack(interaction.client, interaction.guild, interaction.user, stack["id"])
        await interaction.followup.send(text, ephemeral=True)

    @stack_grp.command(name="leave", description="Выйти из своего стака")
    async def stack_leave(self, interaction: discord.Interaction):
        guild = interaction.guild
        stack = db.get_member_stack(guild.id, interaction.user.id)
        if not stack:
            await interaction.response.send_message("Вы не состоите в стаке.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = await remove_member(interaction.client, guild, stack, interaction.user)
        await interaction.followup.send(text.replace("исключён(а) из стака", "вышли из стака"), ephemeral=True, allowed_mentions=NO_PING)

    @setting.command(name="delete", description="Распустить стак (для персонала)")
    @app_commands.describe(name="Название стака")
    @app_commands.autocomplete(name=stack_autocomplete)
    async def stack_delete(self, interaction: discord.Interaction, name: str):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        stack = db.find_stack_by_name(interaction.guild_id, name)
        if not stack:
            await interaction.response.send_message("Такого стака нет.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Распустить стак «{stack['name']}»? Роли и каналы будут удалены.",
            view=ConfirmDisband(stack["id"]), ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Stacks(bot))
