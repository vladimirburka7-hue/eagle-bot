"""Профили участников: ник в Minecraft, первый заход, отряд, награды, нарушения, примечания."""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from content import build_profile_payload, post_components
from emojis import EMOJI
from utils import (
    can_manage, format_date, format_first_join, parse_date, squad_roles, squad_title,
)

NO_PING = discord.AllowedMentions.none()


# ---------- Вспомогательное ----------
def resolve_emoji(text: str) -> str:
    """Принимает короткое имя эмодзи бота (например boost), код <:имя:ID> или обычный смайлик."""
    text = text.strip()
    return EMOJI.get(text, text)


def squad_text(guild: discord.Guild, member: Optional[discord.Member]) -> str:
    """«Первый отряд», если у человека есть роль первого отряда (например «1 group»)."""
    if member is None:
        return "—"
    own = {r.id for r in member.roles}
    parts = [squad_title(slot) for slot, role in squad_roles(guild) if role.id in own]
    return ", ".join(parts) or "Без отряда"


def stack_text(guild_id: int, user_id: int) -> Optional[str]:
    stack = db.get_member_stack(guild_id, user_id)
    if not stack:
        return None
    return stack["name"] + (" (капитан)" if stack["captain_id"] == user_id else "")


def first_join_value(guild, member, profile):
    value = (profile or {}).get("first_joined_at")
    if not value and member and member.joined_at:
        value = member.joined_at.timestamp()
    return value


async def log_to_channel(guild: discord.Guild, text: str):
    channel_id = db.get_setting(guild.id, "log_channel")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        try:
            await channel.send(text, allowed_mentions=NO_PING)
        except discord.HTTPException:
            pass


async def deny(interaction: discord.Interaction):
    if interaction.response.is_done():
        await interaction.followup.send("Это действие только для персонала.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "Это действие только для персонала.", ephemeral=True
        )


async def report_error(interaction: discord.Interaction, error: Exception):
    logging.exception("Ошибка в профиле", exc_info=error)
    text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def reward_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    return [
        app_commands.Choice(name=d["name"][:100], value=d["name"][:100])
        for d in db.list_reward_defs(interaction.guild_id)
        if current in d["name"].lower()
    ][:25]


def edit_summary(guild: discord.Guild, target_id: int) -> str:
    member = guild.get_member(target_id)
    profile = db.get_profile(guild.id, target_id) or {}
    rewards = db.get_user_rewards(guild.id, target_id)
    notes = (profile.get("notes") or "—")
    if len(notes) > 300:
        notes = notes[:300] + "…"
    rewards_text = ", ".join(f"{d['emoji']} {d['name']}" for d in rewards) or "—"
    return "\n".join([
        f"**Редактирование профиля:** <@{target_id}>",
        f"**Ник:** {profile.get('mc_nick') or 'не указан'}",
        f"**Первый заход:** {format_first_join(first_join_value(guild, member, profile))}",
        f"**Отряд:** {squad_text(guild, member)}",
        f"**Нарушений:** {len(db.list_violations(guild.id, target_id))}",
        f"**Награды:** {rewards_text}",
        f"**Примечания:** {notes}",
        "",
        "Выберите, что изменить:",
    ])


# ---------- Окно редактирования ----------
class EditView(discord.ui.View):
    def __init__(self, origin: discord.Interaction, guild: discord.Guild, target_id: int):
        super().__init__(timeout=900)
        self.origin = origin
        self.guild = guild
        self.target_id = target_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not can_manage(interaction.user):
            await deny(interaction)
            return False
        return True

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)

    async def refresh(self):
        """Обновляет главное окно после изменений в дополнительных окнах."""
        try:
            await self.origin.edit_original_response(
                content=edit_summary(self.guild, self.target_id),
                view=self, allowed_mentions=NO_PING,
            )
        except discord.HTTPException:
            pass

    async def redraw(self, interaction: discord.Interaction):
        """Обновляет главное окно из модального окна, открытого его кнопкой."""
        await interaction.response.edit_message(
            content=edit_summary(self.guild, self.target_id),
            view=self, allowed_mentions=NO_PING,
        )

    @discord.ui.button(label="Ник и примечания", style=discord.ButtonStyle.primary, row=0)
    async def nick_notes(self, interaction: discord.Interaction, button: discord.ui.Button):
        profile = db.get_profile(self.guild.id, self.target_id)
        await interaction.response.send_modal(NickNotesModal(self, profile))

    @discord.ui.button(label="Первый заход", style=discord.ButtonStyle.primary, row=0)
    async def first_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        profile = db.get_profile(self.guild.id, self.target_id)
        member = self.guild.get_member(self.target_id)
        await interaction.response.send_modal(
            FirstJoinModal(self, first_join_value(self.guild, member, profile))
        )

    @discord.ui.button(label="Отряд", style=discord.ButtonStyle.primary, row=0)
    async def squad(self, interaction: discord.Interaction, button: discord.ui.Button):
        squads = squad_roles(self.guild)
        if not squads:
            await interaction.response.send_message(
                "Роли отрядов не найдены. Назовите их «1 group», «2 group»… "
                "или выберите через /setting apps squads.", ephemeral=True,
            )
            return
        view = discord.ui.View(timeout=300)
        view.add_item(SquadChoice(self, squads))
        await interaction.response.send_message("Выберите отряд:", view=view, ephemeral=True)

    @discord.ui.button(label="Награды", style=discord.ButtonStyle.primary, row=0)
    async def rewards(self, interaction: discord.Interaction, button: discord.ui.Button):
        defs = db.list_reward_defs(self.guild.id)
        if not defs:
            await interaction.response.send_message(
                "Наград пока нет. Создайте их командой /reward create.", ephemeral=True
            )
            return
        owned = {d["id"] for d in db.get_user_rewards(self.guild.id, self.target_id)}
        view = discord.ui.View(timeout=300)
        view.add_item(RewardChoice(self, defs[:25], owned))
        note = "\nПоказаны первые 25 наград." if len(defs) > 25 else ""
        await interaction.response.send_message(
            "Отметьте награды, которые должны быть у участника:" + note,
            view=view, ephemeral=True,
        )

    @discord.ui.button(label="Нарушение +", style=discord.ButtonStyle.danger, row=1)
    async def warn_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarnModal(self))

    @discord.ui.button(label="Нарушение −", style=discord.ButtonStyle.secondary, row=1)
    async def warn_remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        rows = db.list_violations(self.guild.id, self.target_id)[-25:]
        if not rows:
            await interaction.response.send_message("Нарушений нет.", ephemeral=True)
            return
        view = discord.ui.View(timeout=300)
        view.add_item(ViolationChoice(self, rows))
        await interaction.response.send_message(
            "Какое нарушение удалить?", view=view, ephemeral=True
        )

    @discord.ui.button(label="Обновить", style=discord.ButtonStyle.secondary, row=1)
    async def reload(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.redraw(interaction)


class NickNotesModal(discord.ui.Modal, title="Ник и примечания"):
    def __init__(self, panel: EditView, current: Optional[dict]):
        super().__init__()
        self.panel = panel
        current = current or {}
        self.nick = discord.ui.TextInput(
            label="Ник в Minecraft", required=False, max_length=32,
            default=current.get("mc_nick") or None,
        )
        self.notes = discord.ui.TextInput(
            label="Примечания", style=discord.TextStyle.paragraph,
            required=False, max_length=1000,
            default=current.get("notes") or None,
        )
        self.add_item(self.nick)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        db.set_profile(
            self.panel.guild.id, self.panel.target_id,
            mc_nick=self.nick.value.strip() or None,
            notes=self.notes.value.strip() or None,
        )
        await self.panel.redraw(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)


class FirstJoinModal(discord.ui.Modal, title="Первый заход"):
    def __init__(self, panel: EditView, current_ts):
        super().__init__()
        self.panel = panel
        self.date = discord.ui.TextInput(
            label="Дата (ДД.ММ.ГГГГ), пусто = дата из Discord",
            required=False, max_length=10, placeholder="31.10.2026",
            default=format_date(current_ts) or None,
        )
        self.add_item(self.date)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        try:
            timestamp = parse_date(self.date.value)
        except ValueError:
            await interaction.response.send_message(
                "Неверная дата. Нужен формат ДД.ММ.ГГГГ, например 31.10.2026.", ephemeral=True
            )
            return
        db.set_profile(self.panel.guild.id, self.panel.target_id, first_joined_at=timestamp)
        await self.panel.redraw(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)


class WarnModal(discord.ui.Modal, title="Записать нарушение"):
    def __init__(self, panel: EditView):
        super().__init__()
        self.panel = panel
        self.reason = discord.ui.TextInput(
            label="Причина", style=discord.TextStyle.paragraph, max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        guild = self.panel.guild
        target = self.panel.target_id
        db.add_violation(guild.id, target, self.reason.value.strip(), interaction.user.id, int(time.time()))
        total = len(db.list_violations(guild.id, target))
        await self.panel.redraw(interaction)
        await log_to_channel(
            guild,
            f"Нарушение: <@{target}>, причина: {self.reason.value.strip()} "
            f"(записал(а) {interaction.user.mention}, всего: {total})",
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)


class SquadChoice(discord.ui.Select):
    def __init__(self, panel: EditView, squads):
        options = [
            discord.SelectOption(label=f"{squad_title(slot)} ({role.name})"[:100], value=str(slot))
            for slot, role in squads
        ]
        options.append(discord.SelectOption(label="Без отряда", value="0"))
        super().__init__(placeholder="Отряд участника", options=options)
        self.panel = panel
        self.squads = dict(squads)

    async def callback(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        member = self.panel.guild.get_member(self.panel.target_id)
        if member is None:
            await interaction.response.edit_message(content="Участника нет на сервере.", view=None)
            return
        chosen = int(self.values[0])
        chosen_role = self.squads.get(chosen)
        remove = [r for r in self.squads.values() if r in member.roles and r != chosen_role]
        try:
            if remove:
                await member.remove_roles(*remove, reason="Смена отряда")
            if chosen_role and chosen_role not in member.roles:
                await member.add_roles(chosen_role, reason="Смена отряда")
        except discord.HTTPException:
            await interaction.response.edit_message(
                content="Не удалось изменить роли: роль бота должна стоять выше ролей отрядов "
                        "и у бота должно быть право «Управление ролями».",
                view=None,
            )
            return
        await interaction.response.edit_message(
            content=f"Теперь: {squad_title(chosen) if chosen else 'без отряда'}", view=None
        )
        await self.panel.refresh()


class RewardChoice(discord.ui.Select):
    def __init__(self, panel: EditView, defs, owned):
        options = [
            discord.SelectOption(
                label=d["name"][:100], value=str(d["id"]), default=d["id"] in owned,
            )
            for d in defs
        ]
        super().__init__(
            placeholder="Награды участника", min_values=0,
            max_values=len(options), options=options,
        )
        self.panel = panel
        self.shown = {d["id"]: d for d in defs}
        self.owned = set(owned)

    async def callback(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        guild_id, target = self.panel.guild.id, self.panel.target_id
        chosen = {int(v) for v in self.values}
        gained, lost = [], []
        for reward_id in chosen - self.owned:
            if db.give_reward(guild_id, target, reward_id, interaction.user.id, int(time.time())):
                d = self.shown[reward_id]
                gained.append(f"{d['emoji']} {d['name']}")
        for reward_id in (self.owned & set(self.shown)) - chosen:
            if db.take_reward(guild_id, target, reward_id):
                lost.append(self.shown[reward_id]["name"])
        await interaction.response.edit_message(content=f"Награды обновлены", view=None)
        await self.panel.refresh()
        if gained:
            await log_to_channel(
                self.panel.guild,
                f"<@{target}> получил(а): {', '.join(gained)} (выдал(а) {interaction.user.mention})",
            )


class ViolationChoice(discord.ui.Select):
    def __init__(self, panel: EditView, rows):
        options = [
            discord.SelectOption(
                label=f"#{r['id']} · {format_date(r['created_at'])} · {r['reason']}"[:100],
                value=str(r["id"]),
            )
            for r in rows
        ]
        super().__init__(placeholder="Нарушение для удаления", options=options)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        removed = db.remove_violation(self.panel.guild.id, int(self.values[0]))
        await interaction.response.edit_message(
            content=f"Нарушение удалено" if removed else "Такого нарушения уже нет.", view=None
        )
        await self.panel.refresh()


# ---------- Команды ----------
class Profiles(commands.Cog):
    setting = app_commands.Group(name="profile_setting", description="Редактирование профилей", guild_only=True)
    reward_grp = app_commands.Group(name="reward", description="Награды", guild_only=True)
    warn_grp = app_commands.Group(name="warn", description="Нарушения (для персонала)", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="Показать профиль участника")
    @app_commands.describe(user="Участник (по умолчанию вы)", nick="Или ник в Minecraft")
    @app_commands.guild_only()
    async def profile(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        nick: Optional[str] = None,
    ):
        guild = interaction.guild
        if nick:
            target_id = db.find_user_by_nick(guild.id, nick.strip())
            if not target_id:
                await interaction.response.send_message(
                    f"Игрок с ником «{nick}» не найден. Ник задаётся командой /setting profile edit.",
                    ephemeral=True,
                )
                return
        else:
            target_id = (user or interaction.user).id

        await interaction.response.defer(ephemeral=True)
        member = guild.get_member(target_id)
        if member is None:
            try:
                member = await guild.fetch_member(target_id)
            except discord.HTTPException:
                member = None

        profile = db.get_profile(guild.id, target_id)
        mc_nick = (profile or {}).get("mc_nick")
        rewards = [(d["emoji"], d["name"]) for d in db.get_user_rewards(guild.id, target_id)]
        payload = build_profile_payload(
            name=mc_nick or (member.display_name if member else f"ID {target_id}"),
            user_id=target_id,
            avatar_url=member.display_avatar.url if member else None,
            nick=mc_nick,
            first_join=format_first_join(first_join_value(guild, member, profile)),
            squad=squad_text(guild, member),
            violations=len(db.list_violations(guild.id, target_id)),
            rewards=rewards,
            notes=(profile or {}).get("notes") or "",
            stack=stack_text(guild.id, target_id),
        )
        try:
            await post_components(interaction.client, interaction.channel, payload)
        except discord.HTTPException as e:
            logging.exception("Не удалось отправить профиль")
            await interaction.followup.send(f"Не удалось отправить профиль: {e}", ephemeral=True)
            return
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

    @setting.command(name="edit", description="Редактировать профиль участника")
    @app_commands.describe(user="Участник")
    async def profile_edit(self, interaction: discord.Interaction, user: discord.Member):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        view = EditView(interaction, interaction.guild, user.id)
        await interaction.response.send_message(
            edit_summary(interaction.guild, user.id),
            view=view, ephemeral=True, allowed_mentions=NO_PING,
        )

    # ----- Награды -----
    @reward_grp.command(name="create", description="Создать награду (эмодзи и название)")
    @app_commands.describe(
        emoji="Эмодзи: смайлик, код <:имя:ID> или короткое имя эмодзи бота (boost, steve…)",
        name="Название награды",
    )
    async def reward_create(self, interaction: discord.Interaction, emoji: str, name: str):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        name = name.strip()[:100]
        reward_id = db.add_reward_def(interaction.guild_id, name, resolve_emoji(emoji))
        if reward_id is None:
            await interaction.response.send_message("Награда с таким названием уже есть.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Награда создана: {resolve_emoji(emoji)} {name}", ephemeral=True
        )

    @reward_grp.command(name="delete", description="Удалить награду (и забрать её у всех)")
    @app_commands.describe(reward="Название награды")
    @app_commands.autocomplete(reward=reward_autocomplete)
    async def reward_delete(self, interaction: discord.Interaction, reward: str):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        definition = db.get_reward_def(interaction.guild_id, reward)
        if not definition:
            await interaction.response.send_message("Такой награды нет.", ephemeral=True)
            return
        db.delete_reward_def(interaction.guild_id, definition["id"])
        await interaction.response.send_message(
            f"Награда «{definition['name']}» удалена.", ephemeral=True
        )

    @reward_grp.command(name="list", description="Список наград сервера")
    async def reward_list(self, interaction: discord.Interaction):
        defs = db.list_reward_defs(interaction.guild_id)
        text = "\n".join(f"{d['emoji']} {d['name']}" for d in defs) or "Наград пока нет."
        await interaction.response.send_message(text, ephemeral=True)

    @reward_grp.command(name="give", description="Выдать награду участнику")
    @app_commands.describe(user="Кому выдать", reward="Название награды")
    @app_commands.autocomplete(reward=reward_autocomplete)
    async def reward_give(self, interaction: discord.Interaction, user: discord.Member, reward: str):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        definition = db.get_reward_def(interaction.guild_id, reward)
        if not definition:
            await interaction.response.send_message(
                "Такой награды нет (список: /reward list).", ephemeral=True
            )
            return
        if not db.give_reward(
            interaction.guild_id, user.id, definition["id"], interaction.user.id, int(time.time())
        ):
            await interaction.response.send_message("Эта награда у участника уже есть.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{user.mention} получил(а) награду {definition['emoji']} {definition['name']}",
            ephemeral=True, allowed_mentions=NO_PING,
        )
        await log_to_channel(
            interaction.guild,
            f"{user.mention} получил(а) награду {definition['emoji']} {definition['name']} "
            f"(выдал(а) {interaction.user.mention})",
        )

    @reward_grp.command(name="take", description="Забрать награду у участника")
    @app_commands.describe(user="У кого забрать", reward="Название награды")
    @app_commands.autocomplete(reward=reward_autocomplete)
    async def reward_take(self, interaction: discord.Interaction, user: discord.Member, reward: str):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        definition = db.get_reward_def(interaction.guild_id, reward)
        if not definition or not db.take_reward(interaction.guild_id, user.id, definition["id"]):
            await interaction.response.send_message("У участника нет такой награды.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Награда «{definition['name']}» у {user.mention} убрана.",
            ephemeral=True, allowed_mentions=NO_PING,
        )

    # ----- Нарушения -----
    @warn_grp.command(name="add", description="Записать нарушение участнику")
    @app_commands.describe(user="Нарушитель", reason="Причина")
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        db.add_violation(interaction.guild_id, user.id, reason[:500], interaction.user.id, int(time.time()))
        total = len(db.list_violations(interaction.guild_id, user.id))
        await interaction.response.send_message(
            f"Нарушение записано для {user.mention} (всего: {total}).",
            ephemeral=True, allowed_mentions=NO_PING,
        )
        await log_to_channel(
            interaction.guild,
            f"Нарушение: {user.mention}, причина: {reason[:500]} "
            f"(записал(а) {interaction.user.mention}, всего: {total})",
        )
        try:
            await user.send(
                f"Вам записано нарушение на сервере **{interaction.guild.name}**.\nПричина: {reason[:500]}"
            )
        except discord.HTTPException:
            pass

    @warn_grp.command(name="list", description="Показать нарушения участника")
    @app_commands.describe(user="Участник")
    async def warns(self, interaction: discord.Interaction, user: discord.Member):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        rows = db.list_violations(interaction.guild_id, user.id)
        if not rows:
            await interaction.response.send_message("Нарушений нет.", ephemeral=True)
            return
        lines = [
            f"**#{r['id']}** · {format_date(r['created_at'])} · {r['reason']} (<@{r['moderator_id']}>)"
            for r in rows[-15:]
        ]
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True, allowed_mentions=NO_PING
        )

    @warn_grp.command(name="remove", description="Удалить нарушение по номеру (см. /warn list)")
    @app_commands.describe(number="Номер нарушения")
    async def unwarn(self, interaction: discord.Interaction, number: int):
        if not can_manage(interaction.user):
            await deny(interaction)
            return
        if db.remove_violation(interaction.guild_id, number):
            await interaction.response.send_message(f"Нарушение #{number} удалено.", ephemeral=True)
        else:
            await interaction.response.send_message("Нарушение с таким номером не найдено.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Profiles(bot))
