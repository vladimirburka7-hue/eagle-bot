"""Заявки и обращения.

Любая форма (клан, персонал, жалоба, обращение) отправляется сообщением в
канал логов с кнопками рассмотрения. Каналы ничего сами не создают: временный
канал с заявителем появляется только по кнопке «Создать временный канал».
"""
import io
import json
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from content import (
    DECISION_KINDS, OPEN_IDS, build_app_payload, build_panel_payload,
    edit_components, post_components,
)
from emojis import EMOJI
from utils import squad_roles, squad_title

TITLES = {"clan": "Заявка в клан", "staff": "Заявка в персонал", "report": "Жалоба", "help": "Обращение"}
CHANNEL_PREFIX = {"clan": "клан", "staff": "персонал", "report": "жалоба", "help": "обращение"}
COLORS = {"clan": 0x3BA55D, "staff": 0x5865F2, "report": 0xED4245, "help": 0x99AAB5}
RESULT_LABELS = {
    "accepted": "принята",
    "rejected": "отклонена",
    "resolved": "рассмотрена",
}

SHORT = discord.TextStyle.short
LONG = discord.TextStyle.paragraph

# (подпись до 45 символов, стиль, подсказка, обязательное, макс. длина)
QUESTIONS = {
    "clan": [
        ("Ник в Minecraft", SHORT, "Например: Steve", True, 32),
        ("Возраст", SHORT, "", True, 3),
        ("Опыт игры и кланы", LONG, "Где играли раньше, были ли в кланах", True, 500),
        ("Время онлайн в неделю", SHORT, "Например: 15-20 часов", True, 50),
        ("Почему хотите к нам?", LONG, "", True, 500),
    ],
    "staff": [
        ("Ник и возраст", SHORT, "Например: Steve, 17", True, 60),
        ("Опыт модерации и администрирования", LONG, "Где работали, что делали", True, 600),
        ("Время онлайн", SHORT, "Например: 4 часа в день", True, 60),
        ("Почему вы подходите?", LONG, "", True, 600),
    ],
    "report": [
        ("На кого жалоба", SHORT, "Ник или ID участника", True, 100),
        ("Что произошло", LONG, "Опишите ситуацию подробно", True, 1000),
        ("Доказательства", LONG, "Ссылки на скриншоты или видео", False, 500),
    ],
    "help": [
        ("Суть обращения", LONG, "Опишите вопрос или проблему", True, 1000),
    ],
}


# ---------- Вспомогательное ----------
def is_staff(member: discord.Member, cfg) -> bool:
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    return bool(cfg) and any(r.id == cfg["staff_role_id"] for r in member.roles)


def log_channel_for(guild: discord.Guild, cfg):
    channel_id = (cfg or {}).get("log_channel_id") or db.get_setting(guild.id, "log_channel")
    return guild.get_channel(channel_id) if channel_id else None


def private_overwrites(guild: discord.Guild, members, staff_role) -> dict:
    """Права для приватного канала: видят только перечисленные люди, роль персонала и бот."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True,
            attach_files=True, read_message_history=True,
            manage_channels=True, manage_messages=True,
        ),
    }
    allowed = discord.PermissionOverwrite(
        view_channel=True, send_messages=True,
        read_message_history=True, attach_files=True,
    )
    for member in members:
        overwrites[member] = allowed
    if staff_role:
        overwrites[staff_role] = allowed
    return overwrites


def result_label(kind: str, result: str) -> str:
    if result == "resolved" and kind == "help":
        return "рассмотрено"
    return RESULT_LABELS[result]


def status_text(app: dict) -> str:
    who = f"<@{app['decided_by']}>"
    if app["result"] in ("accepted", "resolved"):
        return f"{EMOJI['ok']} {result_label(app['kind'], app['result']).capitalize()}, {who}"
    if app["result"] == "rejected":
        return f"{EMOJI['no']} Отклонена, {who}"
    if app["claimed_by"]:
        return f"{EMOJI['wait']} Смотрит <@{app['claimed_by']}>"
    return f"{EMOJI['wait']} Ждёт ответа"


def app_payload(app: dict, cfg, ping: bool = False) -> dict:
    kind = app["kind"]
    color = {"accepted": 0x3BA55D, "resolved": 0x3BA55D, "rejected": 0xED4245}.get(
        app["result"], COLORS[kind]
    )
    return build_app_payload(
        kind=kind,
        title=TITLES[kind],
        user_id=app["user_id"],
        answers=json.loads(app.get("answers") or "[]"),
        status=status_text(app),
        color=color,
        avatar_url=app.get("avatar_url"),
        temp_channel_id=app.get("temp_channel_id"),
        decided=app["result"] != "pending",
        ping_role_id=cfg["staff_role_id"] if cfg else None,
        ping=ping,
    )


async def refresh_app_message(client, app: dict):
    """Обновляет сообщение заявки (статус, ссылка на канал, кнопки)."""
    cfg = db.get_ticket_config(app["guild_id"], app["kind"])
    try:
        await edit_components(client, app["channel_id"], app["message_id"], app_payload(app, cfg))
    except discord.HTTPException:
        logging.warning("Не удалось обновить сообщение заявки %s", app["message_id"])


async def ticket_context(interaction: discord.Interaction):
    """Для временных каналов. Возвращает (ticket, cfg) или None."""
    ticket = db.get_ticket(interaction.channel_id)
    if not ticket or ticket["status"] != "open":
        await interaction.response.send_message("Это не активный канал заявки.", ephemeral=True)
        return None
    cfg = db.get_ticket_config(interaction.guild_id, ticket["kind"])
    if not (is_staff(interaction.user, cfg) or interaction.user.id == ticket["user_id"]):
        await interaction.response.send_message(
            "Закрыть канал могут только ответственные и сам заявитель.", ephemeral=True
        )
        return None
    return ticket, cfg


async def app_context(interaction: discord.Interaction):
    """Для сообщений заявок. Возвращает (app, cfg) или None."""
    app = db.get_app(interaction.message.id)
    if not app:
        await interaction.response.send_message("Заявка не найдена в базе.", ephemeral=True)
        return None
    cfg = db.get_ticket_config(interaction.guild_id, app["kind"])
    if not is_staff(interaction.user, cfg):
        await interaction.response.send_message(
            "Эта кнопка только для ответственных.", ephemeral=True
        )
        return None
    return app, cfg


async def build_transcript(channel: discord.TextChannel) -> str:
    lines = []
    async for m in channel.history(limit=2000, oldest_first=True):
        stamp = m.created_at.strftime("%Y-%m-%d %H:%M")
        text = m.content or ""
        for e in m.embeds:
            if e.title or e.description:
                text += f" [{e.title or ''} {e.description or ''}]"
            for f in e.fields:
                text += f"\n    {f.name}: {f.value}"
        for a in m.attachments:
            text += f" [файл: {a.url}]"
        lines.append(f"[{stamp}] {m.author} ({m.author.id}): {text}".rstrip())
    return "\n".join(lines) or "(пусто)"


async def decide_app(
    interaction: discord.Interaction,
    app: dict,
    cfg,
    result: str,
    reason: Optional[str] = None,
    extra_role_ids=(),
):
    """Принять / отклонить / отметить рассмотренной."""
    guild = interaction.guild
    kind = app["kind"]
    db.update_app(app["message_id"], result=result, decided_by=interaction.user.id)

    member = guild.get_member(app["user_id"])
    given = []
    failed_roles = False
    if result == "accepted":
        role_ids = []
        if cfg and cfg.get("accept_role_id"):
            role_ids.append(cfg["accept_role_id"])
        role_ids.extend(extra_role_ids)
        roles = [guild.get_role(i) for i in dict.fromkeys(role_ids)]
        roles = [r for r in roles if r]
        if roles and member:
            try:
                await member.add_roles(*roles, reason="Заявка принята")
                given = roles
            except discord.HTTPException:
                failed_roles = True

    await refresh_app_message(interaction.client, db.get_app(app["message_id"]))

    channel = guild.get_channel(app["channel_id"])
    title = {
        "accepted": "Заявка принята",
        "rejected": "Заявка отклонена",
        "resolved": "Жалоба рассмотрена" if kind == "report" else "Рассмотрено",
    }[result]
    description = f"Решение принял(а) {interaction.user.mention}"
    if given:
        description += "\n**Выданы роли:** " + ", ".join(r.mention for r in given)
    if reason:
        description += f"\n**Причина:** {reason}"
    if channel:
        try:
            await channel.send(
                embed=discord.Embed(
                    title=title,
                    description=description,
                    color=0xED4245 if result == "rejected" else 0x3BA55D,
                ),
                reference=discord.MessageReference(
                    message_id=app["message_id"], channel_id=channel.id,
                    guild_id=guild.id, fail_if_not_exists=False,
                ),
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if failed_roles:
                await channel.send(
                    "Не удалось выдать роли: роль бота должна стоять выше выдаваемых ролей, "
                    "и у бота должно быть право «Управление ролями»."
                )
            elif result == "accepted" and member is None:
                await channel.send("Заявитель уже не на сервере, роли не выданы.")
        except discord.HTTPException:
            pass

    if member:
        text = f"{TITLES[kind]} на сервере **{guild.name}**: {result_label(kind, result)}."
        if reason:
            text += f"\nПричина: {reason}"
        try:
            await member.send(text)
        except discord.HTTPException:
            pass  # закрыты личные сообщения


async def close_ticket(client, channel: discord.TextChannel, closed_by: discord.Member):
    guild = channel.guild
    ticket = db.get_ticket(channel.id)
    if not ticket:
        return
    cfg = db.get_ticket_config(guild.id, ticket["kind"])
    db.update_ticket(channel.id, status="closed")
    app = db.get_app(ticket["app_message_id"]) if ticket.get("app_message_id") else None
    if app:
        db.update_app(app["message_id"], temp_channel_id=None)
        await refresh_app_message(client, db.get_app(app["message_id"]))

    transcript = await build_transcript(channel)
    log_channel = log_channel_for(guild, cfg)
    if log_channel:
        embed = discord.Embed(
            title=f"Канал закрыт: {TITLES[ticket['kind']]}",
            color=COLORS[ticket["kind"]],
        )
        embed.add_field(name="Заявитель", value=f"<@{ticket['user_id']}>")
        embed.add_field(name="Закрыл(а)", value=closed_by.mention)
        if app:
            link = f"https://discord.com/channels/{guild.id}/{app['channel_id']}/{app['message_id']}"
            embed.add_field(name="Заявка", value=f"[Перейти к заявке]({link})", inline=False)
        file = discord.File(
            io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt"
        )
        try:
            await log_channel.send(
                embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            logging.warning("Не удалось отправить лог закрытого канала")
    try:
        await channel.delete(reason=f"Закрыт: {closed_by}")
    except discord.HTTPException:
        logging.warning("Не удалось удалить канал")


# ---------- Формы ----------
class TicketModal(discord.ui.Modal):
    def __init__(self, kind: str):
        super().__init__(title=TITLES[kind][:45], timeout=900)
        self.kind = kind
        self.inputs = []
        for label, style, placeholder, required, max_len in QUESTIONS[kind]:
            item = discord.ui.TextInput(
                label=label,
                style=style,
                placeholder=placeholder or None,
                required=required,
                max_length=max_len,
            )
            self.add_item(item)
            self.inputs.append(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        kind = self.kind

        cfg = db.get_ticket_config(guild.id, kind)
        review = log_channel_for(guild, cfg) if cfg else None
        if not cfg or not review:
            await interaction.followup.send(
                "Система заявок ещё не настроена. Сообщите администрации.", ephemeral=True
            )
            return
        if db.find_pending_app(guild.id, user.id, kind):
            await interaction.followup.send(
                "Ваша предыдущая заявка этого типа ещё на рассмотрении.", ephemeral=True
            )
            return

        answers = [
            [question[0], item.value or "—"]
            for question, item in zip(QUESTIONS[kind], self.inputs)
        ]
        avatar_url = user.display_avatar.url
        draft = {
            "kind": kind, "user_id": user.id, "answers": json.dumps(answers, ensure_ascii=False),
            "avatar_url": avatar_url, "temp_channel_id": None, "result": "pending",
            "claimed_by": None, "decided_by": None,
        }
        try:
            message = await post_components(
                interaction.client, review, app_payload(draft, cfg, ping=True)
            )
        except discord.HTTPException as e:
            logging.exception("Не удалось отправить заявку")
            await interaction.followup.send(
                f"Не удалось отправить заявку (проверьте права бота в канале заявок): {e}",
                ephemeral=True,
            )
            return
        db.create_app(
            int(message["id"]), review.id, guild.id, user.id, kind,
            draft["answers"], avatar_url,
        )
        await interaction.followup.send(
            f"{EMOJI['tick']} Готово, заявка отправлена. Ответ придёт в личку.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы", exc_info=error)
        text = "Что-то пошло не так, попробуйте ещё раз."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class RejectModal(discord.ui.Modal, title="Отклонить заявку"):
    reason = discord.ui.TextInput(
        label="Причина (необязательно)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
    )

    def __init__(self, message_id: int):
        super().__init__()
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        app = db.get_app(self.message_id)
        cfg = db.get_ticket_config(interaction.guild_id, app["kind"]) if app else None
        if not app or not is_staff(interaction.user, cfg):
            await interaction.response.send_message("Нет прав или заявка не найдена.", ephemeral=True)
            return
        if app["result"] != "pending":
            await interaction.response.send_message("Решение уже принято.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await decide_app(interaction, app, cfg, "rejected", self.reason.value or None)
        await interaction.followup.send("Заявка отклонена.", ephemeral=True)


# ---------- Кнопки ----------
class PanelView(discord.ui.View):
    """Нужна только для обработки кнопок панели (сама панель отправляется из content.py)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Заявка в клан", custom_id=OPEN_IDS["clan"])
    async def open_clan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("clan"))

    @discord.ui.button(label="Заявка в персонал", custom_id=OPEN_IDS["staff"])
    async def open_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("staff"))

    @discord.ui.button(label="Жалоба", custom_id=OPEN_IDS["report"])
    async def open_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("report"))

    @discord.ui.button(label="Обращение", custom_id=OPEN_IDS["help"])
    async def open_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal("help"))


class SquadSelect(discord.ui.Select):
    """Выбор отряда при принятии заявки в клан."""

    def __init__(self, squads, message_id: int):
        options = [
            discord.SelectOption(label=f"{squad_title(slot)} ({role.name})"[:100], value=str(role.id))
            for slot, role in squads
        ]
        options.append(discord.SelectOption(label="Без отряда", value="0"))
        super().__init__(placeholder="В какой отряд определить?", options=options)
        self.message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        app = db.get_app(self.message_id)
        if not app or app["result"] != "pending":
            await interaction.response.edit_message(
                content="Решение уже принято или заявка не найдена.", view=None
            )
            return
        cfg = db.get_ticket_config(interaction.guild_id, app["kind"])
        if not is_staff(interaction.user, cfg):
            await interaction.response.edit_message(content="Нет прав.", view=None)
            return
        role_id = int(self.values[0])
        await interaction.response.edit_message(content="Принимаю заявку…", view=None)
        await decide_app(
            interaction, app, cfg, "accepted",
            extra_role_ids=[role_id] if role_id else [],
        )
        await interaction.edit_original_response(content="Заявка принята.")


class AppControls(discord.ui.View):
    """Обработчики кнопок под заявкой (внешний вид собирает content.build_app_payload)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Взять на рассмотрение", custom_id="ap:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await app_context(interaction)
        if not ctx:
            return
        app, cfg = ctx
        if app["result"] != "pending":
            await interaction.response.send_message("Решение уже принято.", ephemeral=True)
            return
        db.update_app(app["message_id"], claimed_by=interaction.user.id)
        await interaction.response.defer()
        await refresh_app_message(interaction.client, db.get_app(app["message_id"]))

    @discord.ui.button(label="Принять", custom_id="ap:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await app_context(interaction)
        if not ctx:
            return
        app, cfg = ctx
        if app["result"] != "pending":
            await interaction.response.send_message("Решение уже принято.", ephemeral=True)
            return
        if app["kind"] == "clan":
            squads = squad_roles(interaction.guild)
            if squads:
                view = discord.ui.View(timeout=120)
                view.add_item(SquadSelect(squads, app["message_id"]))
                await interaction.response.send_message(
                    "Выберите отряд для нового участника:", view=view, ephemeral=True
                )
                return
        await interaction.response.defer(ephemeral=True)
        result = "accepted" if app["kind"] in DECISION_KINDS else "resolved"
        await decide_app(interaction, app, cfg, result)
        await interaction.followup.send("Готово.", ephemeral=True)

    @discord.ui.button(label="Отклонить", custom_id="ap:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await app_context(interaction)
        if not ctx:
            return
        app, cfg = ctx
        if app["result"] != "pending":
            await interaction.response.send_message("Решение уже принято.", ephemeral=True)
            return
        await interaction.response.send_modal(RejectModal(app["message_id"]))

    @discord.ui.button(label="Создать временный канал", custom_id="ap:temp")
    async def temp(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await app_context(interaction)
        if not ctx:
            return
        app, cfg = ctx
        guild = interaction.guild

        if app["temp_channel_id"]:
            existing = guild.get_channel(app["temp_channel_id"])
            if existing:
                await interaction.response.send_message(
                    f"Канал уже создан: {existing.mention}", ephemeral=True
                )
                return
        await interaction.response.defer(ephemeral=True)

        category = guild.get_channel(cfg["category_id"]) if cfg else None
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                "Не настроена категория для временных каналов (/setting apps setup).", ephemeral=True
            )
            return
        applicant = guild.get_member(app["user_id"])
        if applicant is None:
            await interaction.followup.send("Заявитель уже не на сервере.", ephemeral=True)
            return

        staff_role = guild.get_role(cfg["staff_role_id"])
        kind = app["kind"]
        try:
            channel = await guild.create_text_channel(
                name=f"{CHANNEL_PREFIX[kind]}-{db.next_ticket_number(guild.id, kind):02d}",
                category=category,
                overwrites=private_overwrites(guild, [applicant, interaction.user], staff_role),
                topic=f"Временный канал по заявке: {applicant} (ID {applicant.id})",
                reason=f"Временный канал по заявке {applicant}",
            )
        except discord.Forbidden:
            await interaction.followup.send("У бота нет прав создавать каналы.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось создать канал: {e}", ephemeral=True)
            return

        message_url = (
            f"https://discord.com/channels/{guild.id}/{app['channel_id']}/{app['message_id']}"
        )
        db.create_ticket(channel.id, guild.id, applicant.id, kind, app_message_id=app["message_id"])
        db.update_app(app["message_id"], temp_channel_id=channel.id)
        await refresh_app_message(interaction.client, db.get_app(app["message_id"]))

        embed = discord.Embed(
            title=f"{EMOJI['discord']} Временный канал",
            description=(
                f"Заявка: [{TITLES[kind]}]({message_url})\n"
                "-# Решили вопрос — нажмите «Закрыть». Переписка сохранится в логах."
            ),
            color=COLORS[kind],
        )
        await channel.send(
            content=applicant.mention + (f" {staff_role.mention}" if staff_role else ""),
            embed=embed,
            view=TicketControls(),
            allowed_mentions=discord.AllowedMentions(
                users=[applicant], roles=[staff_role] if staff_role else []
            ),
        )
        await interaction.followup.send(f"Канал создан: {channel.mention}", ephemeral=True)


class ConfirmClose(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Да, закрыть", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Закрываю канал…", view=None)
        await close_ticket(interaction.client, interaction.channel, interaction.user)


class TicketControls(discord.ui.View):
    """Кнопка в временном канале."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть", style=discord.ButtonStyle.secondary, custom_id="tk:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await ticket_context(interaction)
        if not ctx:
            return
        await interaction.response.send_message(
            "Закрыть канал? Он будет удалён, переписка сохранится в логах.",
            view=ConfirmClose(),
            ephemeral=True,
        )


# ---------- Команды ----------
KIND_CHOICES = [
    app_commands.Choice(name="Заявка в клан", value="clan"),
    app_commands.Choice(name="Заявка в персонал", value="staff"),
    app_commands.Choice(name="Жалоба", value="report"),
    app_commands.Choice(name="Обращение", value="help"),
]


class Tickets(commands.Cog):
    setting = app_commands.Group(name="app_setting", description="Настройки заявок и отрядов", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(PanelView())
        self.bot.add_view(AppControls())
        self.bot.add_view(TicketControls())

    @setting.command(name="setup", description="Настроить один тип заявок")
    @app_commands.describe(
        kind="Тип заявки",
        category="Категория для временных каналов (создаются только по кнопке)",
        staff_role="Роль, которая рассматривает заявки и видит временные каналы",
        log_channel="Канал, куда приходят заявки и логи (например логи-в-отряд)",
        accept_role="Роль, которую получит каждый принятый, например Eagles (необязательно)",
    )
    @app_commands.choices(kind=KIND_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        category: discord.CategoryChannel,
        staff_role: discord.Role,
        log_channel: discord.TextChannel,
        accept_role: Optional[discord.Role] = None,
    ):
        me = interaction.guild.me
        if not category.permissions_for(me).manage_channels:
            await interaction.response.send_message(
                "У бота нет права «Управление каналами» в этой категории.", ephemeral=True
            )
            return
        if not log_channel.permissions_for(me).send_messages:
            await interaction.response.send_message(
                f"У бота нет права писать в {log_channel.mention}.", ephemeral=True
            )
            return
        db.set_ticket_config(
            interaction.guild_id, kind.value, category.id, staff_role.id,
            log_channel.id, accept_role.id if accept_role else None,
        )
        text = (
            f"**{kind.name}** настроена:\n"
            f"• Заявки и логи: {log_channel.mention}\n"
            f"• Временные каналы: {category.mention}\n"
            f"• Ответственные: {staff_role.mention}"
        )
        if accept_role:
            text += f"\n• Роль при принятии: {accept_role.mention}"
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @setting.command(name="panel", description="Отправить панель подачи заявок")
    @app_commands.describe(channel="Канал для панели (по умолчанию текущий)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_panel(
        self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None
    ):
        target = channel or interaction.channel
        missing = [
            TITLES[k] for k in TITLES if not db.get_ticket_config(interaction.guild_id, k)
        ]
        await interaction.response.defer(ephemeral=True)
        try:
            await post_components(self.bot, target, build_panel_payload())
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить панель: {e}", ephemeral=True)
            return
        text = f"Панель отправлена в {target.mention}."
        if missing:
            text += "\nЕщё не настроено (/setting apps setup): " + ", ".join(missing)
        await interaction.followup.send(text, ephemeral=True)

    @setting.command(name="squads", description="Выбрать роли отрядов (до 5) для принятия в клан")
    @app_commands.describe(
        squad1="Роль отряда 1",
        squad2="Роль отряда 2",
        squad3="Роль отряда 3",
        squad4="Роль отряда 4",
        squad5="Роль отряда 5",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def squads_setup(
        self,
        interaction: discord.Interaction,
        squad1: discord.Role,
        squad2: Optional[discord.Role] = None,
        squad3: Optional[discord.Role] = None,
        squad4: Optional[discord.Role] = None,
        squad5: Optional[discord.Role] = None,
    ):
        roles = [r for r in (squad1, squad2, squad3, squad4, squad5) if r]
        db.set_squads(interaction.guild_id, [r.id for r in roles])
        lines = [f"• Отряд {i}: {r.mention}" for i, r in enumerate(roles, start=1)]
        await interaction.response.send_message(
            "Отряды сохранены:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @setting.command(name="show", description="Показать текущие настройки заявок")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ticket_info(self, interaction: discord.Interaction):
        guild = interaction.guild
        lines = []
        for kind, title in TITLES.items():
            cfg = db.get_ticket_config(guild.id, kind)
            if not cfg:
                lines.append(f"**{title}:** не настроено")
                continue
            category = guild.get_channel(cfg["category_id"])
            role = guild.get_role(cfg["staff_role_id"])
            log = guild.get_channel(cfg["log_channel_id"]) if cfg["log_channel_id"] else None
            accept = guild.get_role(cfg["accept_role_id"]) if cfg["accept_role_id"] else None
            lines.append(
                f"**{title}:** заявки в {log.mention if log else '—'}, "
                f"временные каналы в {category.mention if category else '—'}, "
                f"ответственные {role.mention if role else '—'}"
                + (f", роль при принятии {accept.mention}" if accept else "")
            )
        squads = []
        for slot, role_id in db.get_squads(guild.id):
            role = guild.get_role(role_id)
            squads.append(f"{slot}: {role.mention if role else 'роль удалена'}")
        lines.append("**Отряды:** " + (", ".join(squads) if squads else "не настроены"))
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
