"""Памятка-обзор сервера: отряды и капитаны, стаки, структура каналов, быстрые ссылки и баннер.

Сообщение обновляется само: при изменении стаков и ролей, а также раз в 15 минут.
"""
import io
import logging
import os
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from content import (
    EPHEMERAL, build_button_payload, build_memo_payload, edit_components, parse_sections, post_components,
    respond_components,
)
from emojis import EMOJI, emoji_to_button
from utils import (
    apps_overview, can_manage, fit_lines, memo_overview, roles_structure_lines, structure_auto,
)

NO_PING = discord.AllowedMentions.none()
# Тексты для НОВЫХ серверов — нейтральные. Серверы, где памятка уже была, сохраняют прежние тексты (migrate_neutral_v1).
CLAN_INTRO = "Всё о сервере в одном месте: отряды, стаки, структура и правила."
DEFAULT_INTRO = "Всё о сервере в одном месте: кто мы, структура и правила."
MAX_BANNER_BYTES = 8 * 1024 * 1024
# старые кнопки по умолчанию -> новый вид (свои кнопки не трогаем)
OLD_BUTTONS = {
    ("Структура ролей", "steve"): ("Роли", "admin"),
    ("Заявки", "whitebutterfly"): ("Заявки", "heart"),
    ("Правила", "rules"): ("Правила", "rules"),
}
OLD_TEXTS = {
    "Здесь собрано всё о сервере: отряды и капитаны, стаки, структура каналов и правила.": CLAN_INTRO,
    "Здесь собрано всё о сервере: чем мы занимаемся, структура каналов и правила.": DEFAULT_INTRO,
}
MAX_CUSTOM_BUTTONS = 10  # лимит Discord по компонентам одного сообщения
CLAN_ACTIVITIES = "• **Minecraft** — клан на сервере Funtime: отряды с капитанами, свои стаки."
DEFAULT_ACTIVITIES = ""  # пусто = блок «Чем мы занимаемся» скрыт, пока его не заполнят
APPS_TEXT = (
    "## Как подать\n"
    "1. Кнопка в канале заявок\n"
    "2. Короткая форма\n"
    "3. Ответ — в личные сообщения (откройте их)"
)
ROLES_TEXT = "Роли сверху вниз и сколько у кого участников."
RULES_TEXT = (
    "## Уважение и поведение\n"
    "1.1 Относитесь ко всем участникам с уважением\n"
    "1.2 Оскорбления, травля и провокации запрещены\n"
    "1.3 Дискриминация и разжигание вражды запрещены\n"
    "1.4 Конфликты решайте спокойно, при необходимости — через администрацию\n"
    "1.5 Решения руководства не обсуждаются в общих каналах\n"
    "## Чат и контент\n"
    "2.1 Пишите в каналах строго по теме\n"
    "2.2 Спам, флуд, капс и злоупотребление упоминаниями запрещены\n"
    "2.3 Реклама сторонних серверов, сайтов и услуг запрещена\n"
    "2.4 NSFW, шок-контент, угрозы и призывы к насилию запрещены\n"
    "2.5 Чужие личные данные и переписки нельзя публиковать без согласия\n"
    "## Голосовые каналы\n"
    "3.1 Не кричите и не создавайте шум, музыку и саундпад — только с согласия остальных\n"
    "3.2 Запись разговоров без согласия участников запрещена\n"
    "3.3 Не перемещайтесь между каналами, чтобы мешать другим\n"
    "3.4 Выполняйте просьбы администрации и капитанов\n"
    "## Игровые правила (Funtime)\n"
    "4.1 Соблюдайте правила сервера Funtime — за их нарушение отвечаете сами\n"
    "4.2 Читы, запрещённые модификации и использование багов запрещены\n"
    "4.3 Грабёж, гриферство и кражи у участников клана запрещены\n"
    "4.4 Не разглашайте координаты баз и внутреннюю информацию клана\n"
    "4.5 Достойно представляйте клан в игре и в любых чатах\n"
    "## Дисциплина и отряды\n"
    "5.1 Каждый участник состоит в отряде и подчиняется своему капитану\n"
    "5.2 Участвуйте в активностях клана и выполняйте указания руководства\n"
    "5.3 О долгом отсутствии предупреждайте капитана заранее\n"
    "5.4 Капитаны отчитываются шефам, шефы — Main Personal\n"
    "5.5 Не обходите иерархию: вопросы идут сначала капитану, затем выше\n"
    "## Защита аккаунта и сервера\n"
    "6.1 Никому не передавайте пароли, токены и доступ к аккаунтам\n"
    "6.2 Не переходите по подозрительным ссылкам и не скачивайте файлы от незнакомцев\n"
    "6.3 Боты и вебхуки добавляются только с согласия администрации\n"
    "6.4 О взломах, утечках и подозрительных участниках сообщайте администрации\n"
    "## Система наказаний\n"
    "Предупреждение → Мут → Кик → Бан\n"
    "7.1 Решение принимает администрация с учётом тяжести и повторности\n"
    "7.2 Тяжёлые нарушения (угрозы, читы, сливы, взломы) — бан без предупреждения\n"
    "7.3 Обход наказания с другого аккаунта ведёт к бану\n"
    "## Обжалование\n"
    "8.1 Не согласны с наказанием — подайте жалобу через тикет\n"
    "8.2 Правила могут меняться, следите за новостями сервера"
)
NEUTRAL_RULES_TEXT = (
    "## Уважение и поведение\n"
    "1.1 Относитесь ко всем участникам с уважением\n"
    "1.2 Оскорбления, травля и провокации запрещены\n"
    "1.3 Дискриминация и разжигание вражды запрещены\n"
    "1.4 Конфликты решайте спокойно, при необходимости — через администрацию\n"
    "## Чат и контент\n"
    "2.1 Пишите в каналах строго по теме\n"
    "2.2 Спам, флуд, капс и злоупотребление упоминаниями запрещены\n"
    "2.3 Реклама сторонних серверов, сайтов и услуг запрещена\n"
    "2.4 NSFW, шок-контент, угрозы и призывы к насилию запрещены\n"
    "2.5 Чужие личные данные и переписки нельзя публиковать без согласия\n"
    "## Голосовые каналы\n"
    "3.1 Не кричите и не создавайте шум, музыку и саундпад — только с согласия остальных\n"
    "3.2 Запись разговоров без согласия участников запрещена\n"
    "## Защита аккаунта и сервера\n"
    "4.1 Никому не передавайте пароли, токены и доступ к аккаунтам\n"
    "4.2 Не переходите по подозрительным ссылкам и не скачивайте файлы от незнакомцев\n"
    "4.3 О взломах и подозрительных участниках сообщайте администрации\n"
    "## Наказания\n"
    "Предупреждение → Мут → Кик → Бан\n"
    "5.1 Решение принимает администрация с учётом тяжести и повторности\n"
    "5.2 Обход наказания с другого аккаунта ведёт к бану\n"
    "5.3 Не согласны с наказанием — подайте жалобу через тикет"
)
DEFAULT_BUTTONS = [
    {"label": "Правила", "emoji": "rules", "style": 2, "kind": "text", "title": "Правила сервера", "content": NEUTRAL_RULES_TEXT},
    {"label": "Заявки", "emoji": "whiteheart", "style": 2, "kind": "apps", "title": "Заявки", "content": APPS_TEXT},
    {"label": "Роли", "emoji": "admin", "style": 2, "kind": "roles", "title": "Роли", "content": ROLES_TEXT},
]
STYLE_CHOICES = [
    app_commands.Choice(name="Синяя", value=1), app_commands.Choice(name="Серая", value=2),
    app_commands.Choice(name="Зелёная", value=3), app_commands.Choice(name="Красная", value=4),
]
KIND_CHOICES = [
    app_commands.Choice(name="С текстом", value="text"), app_commands.Choice(name="Ссылка", value="link"),
    app_commands.Choice(name="Заявки (живая статистика)", value="apps"),
    app_commands.Choice(name="Структура ролей (автоматически)", value="roles"),
]


def resolve_button_emoji(guild: discord.Guild, text: str):
    """Эмодзи для кнопки: смайлик, имя/код эмодзи бота или имя эмодзи этого сервера (например cosimbad)."""
    found = emoji_to_button(text)
    if found:
        return found
    custom = discord.utils.get(guild.emojis, name=(text or "").strip())
    if custom:
        return {"id": str(custom.id), "name": custom.name, "animated": custom.animated}
    return None


def custom_buttons(guild: discord.Guild, with_emoji: bool = True):
    guild_id = guild.id
    items = []
    for btn in db.list_memo_buttons(guild_id)[:MAX_CUSTOM_BUTTONS]:
        label, emoji = OLD_BUTTONS.get((btn["label"], btn["emoji"]), (btn["label"], btn["emoji"]))
        item = {"label": label, "style": 5 if btn["url"] else btn["style"],
                "emoji": resolve_button_emoji(guild, emoji) if with_emoji and emoji else None}
        if btn["url"]:
            item["url"] = btn["url"]
        else:
            item["custom_id"] = f"mb:{btn['id']}"
        items.append(item)
    return items


def build_memo(guild: discord.Guild, cfg: dict, with_emoji: bool = True):
    squads, stacks, max_members = memo_overview(guild)
    custom = (cfg.get("structure") or "").strip()
    intro = cfg["intro"] if cfg.get("intro") is not None else DEFAULT_INTRO
    intro = OLD_TEXTS.get((intro or "").strip(), intro)  # старые длинные тексты по умолчанию -> короткие
    activities = cfg["activities"] if cfg.get("activities") is not None else DEFAULT_ACTIVITIES
    banner = cfg.get("banner")
    # None = автоматически: блок виден, только если на сервере есть отряды / стаки
    show_squads = bool(squads) if cfg.get("show_squads") is None else bool(cfg["show_squads"])
    show_stacks = (bool(stacks) or db.get_stack_settings(guild.id) is not None) if cfg.get("show_stacks") is None \
        else bool(cfg["show_stacks"])
    payload = build_memo_payload(
        title=cfg.get("title") or guild.name, intro=intro, total=guild.member_count,
        squads=squads, stacks=stacks, max_members=max_members,
        structure_text=custom or structure_auto(guild), structure_code=bool(custom),
        banner_name=cfg.get("banner_name") if banner else None,
        extra=custom_buttons(guild, with_emoji), activities=(activities or "").strip() or None,
        show_squads=show_squads, show_stacks=show_stacks,
        show_structure=cfg.get("show_structure") is None or bool(cfg["show_structure"]),
        show_members=cfg.get("show_members") is None or bool(cfg["show_members"]),
        squads_title=cfg.get("squads_title"), stacks_title=cfg.get("stacks_title"),
        structure_title=cfg.get("structure_title"), buttons_title=cfg.get("buttons_title"),
        activities_title=cfg.get("activities_title"),
    )
    upload = (bytes(banner), cfg["banner_name"]) if banner else None
    return payload, upload


def seed_default_buttons(guild_id: int):
    """При первом запуске добавляет кнопки «Правила», «Заявки» и «Структура ролей» (один раз)."""
    cfg = db.get_memo(guild_id)
    if not cfg or cfg.get("buttons_seeded"):
        return
    if not db.list_memo_buttons(guild_id):
        for item in DEFAULT_BUTTONS:
            db.add_memo_button(guild_id, **item)
    db.set_memo(guild_id, buttons_seeded=1)


async def refresh_memo(bot, guild: discord.Guild, repost: bool = False) -> Optional[str]:
    """Обновляет памятку. Возвращает текст ошибки или None."""
    seed_default_buttons(guild.id)
    cfg = db.get_memo(guild.id)
    if not cfg or not cfg["channel_id"]:
        return "Канал памятки не выбран (/info_setting post)."
    channel = guild.get_channel(cfg["channel_id"])
    if channel is None:
        return "Канал памятки не найден."
    # если Discord не принял эмодзи на кнопках, повторяем без них
    for with_emoji in (True, False):
        payload, upload = build_memo(guild, cfg, with_emoji)
        if cfg["message_id"] and not repost:
            try:
                await edit_components(bot, channel.id, cfg["message_id"], payload, upload)
                return None
            except discord.HTTPException as e:
                if e.status == 400 and with_emoji:
                    continue
                logging.warning("Не удалось отредактировать памятку, отправляю заново")
        try:
            message = await post_components(bot, channel, payload, upload)
        except discord.HTTPException as e:
            if e.status == 400 and with_emoji:
                continue
            return f"Не удалось отправить памятку: {e}"
        db.set_memo(guild.id, message_id=int(message["id"]))
        return None
    return "Не удалось отправить памятку."


def migrate_text_v3():
    """Один раз: убрать упоминание Dota 2 из блока «Чем занимаемся» и обновить вступление к ролям."""
    if db.get_flag("memo_text_v3"):
        return
    for cfg in db.list_all_memo_settings():
        text = cfg.get("activities")
        if text and "dota" in text.casefold():
            kept = [line for line in text.splitlines() if "dota" not in line.casefold()]
            db.set_memo(cfg["guild_id"], activities="\n".join(kept).strip())
    for btn in db.list_all_memo_buttons():
        if (btn.get("kind") or "text") == "roles" and (btn["content"] or "") == "Роли сервера сверху вниз. В строке указано, сколько участников имеют роль.":
            db.update_memo_button(btn["id"], content=ROLES_TEXT)
    db.set_flag("memo_text_v3")


def migrate_neutral_v1():
    """Один раз: серверам, где памятка уже была, сохраняем прежние (клановые) тексты по умолчанию."""
    if db.get_flag("memo_neutral_v1"):
        return
    for cfg in db.list_all_memo_settings():
        fields = {}
        if cfg.get("intro") is None:
            fields["intro"] = CLAN_INTRO
        if cfg.get("activities") is None:
            fields["activities"] = CLAN_ACTIVITIES
        if fields:
            db.set_memo(cfg["guild_id"], **fields)
    db.set_flag("memo_neutral_v1")


def migrate_buttons():
    """Один раз: серый цвет у стандартных кнопок, новый эмодзи у «Структуры ролей», новый вид текстов."""
    if db.get_flag("memo_buttons_v2"):
        return
    for btn in db.list_all_memo_buttons():
        kind = btn.get("kind") or "text"
        fields = {}
        if kind in ("apps", "roles") or (btn["label"] == "Правила" and (btn["content"] or "").startswith("**၊ Общие правила**")):
            fields["style"] = 2
        if kind == "roles" and btn["emoji"] == "whiteheart":
            fields["emoji"] = "steve"
        content = btn["content"] or ""
        if kind == "apps" and content.startswith("**Как это работает**"):
            fields["content"] = APPS_TEXT
        if btn["label"] == "Правила" and content.startswith("**၊ Общие правила**"):
            fields["content"] = RULES_TEXT
        if fields:
            db.update_memo_button(btn["id"], **fields)
    db.set_flag("memo_buttons_v2")


class MemoTextModal(discord.ui.Modal, title="Текст памятки"):
    def __init__(self, guild: discord.Guild, cfg: Optional[dict]):
        super().__init__(timeout=900)
        cfg = cfg or {}
        self.title_input = discord.ui.TextInput(
            label="Заголовок", max_length=60, default=(cfg.get("title") or guild.name)[:60],
        )
        self.intro = discord.ui.TextInput(
            label="Вступление", style=discord.TextStyle.paragraph, required=False, max_length=500,
            default=(cfg["intro"] if cfg.get("intro") is not None else DEFAULT_INTRO) or None,
        )
        self.activities = discord.ui.TextInput(
            label="Чем занимаетесь (пусто = убрать блок)", style=discord.TextStyle.paragraph,
            required=False, max_length=500,
            default=(cfg["activities"] if cfg.get("activities") is not None else DEFAULT_ACTIVITIES) or None,
            placeholder="Например: • **Minecraft** — играем вместе на выживании",
        )
        self.structure = discord.ui.TextInput(
            label="Структура сервера (пусто = автоматически)", style=discord.TextStyle.paragraph,
            required=False, max_length=1000, default=cfg.get("structure") or None,
            placeholder="Оставьте пустым: бот соберёт структуру из видимых каналов",
        )
        for item in (self.title_input, self.intro, self.activities, self.structure):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        db.set_memo(
            interaction.guild_id, title=self.title_input.value.strip() or None,
            intro=self.intro.value.strip(), activities=self.activities.value.strip(),
            structure=self.structure.value.strip() or None,
        )
        error = await refresh_memo(interaction.client, interaction.guild)
        await interaction.followup.send(error or "Текст памятки сохранён и обновлён.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы памятки", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class ButtonModal(discord.ui.Modal):
    """Создание и правка кнопки памятки (с текстом или ссылкой)."""

    def __init__(self, existing: Optional[dict], kind: str, style: Optional[int],
                 image: Optional[discord.Attachment], remove_image: bool):
        super().__init__(title="Кнопка памятки", timeout=900)
        self.existing, self.kind, self.style = existing, kind, style
        self.image, self.remove_image = image, remove_image
        ex = existing or {}
        self.label = discord.ui.TextInput(label="Подпись на кнопке", max_length=40, default=ex.get("label") or None)
        self.emoji = discord.ui.TextInput(
            label="Эмодзи (необязательно)", required=False, max_length=60,
            placeholder="🦅, boost, имя эмодзи сервера", default=ex.get("emoji") or None,
        )
        self.add_item(self.label)
        self.add_item(self.emoji)
        if kind == "link":
            self.url = discord.ui.TextInput(label="Ссылка", max_length=300, placeholder="https://...", default=ex.get("url") or None)
            self.add_item(self.url)
        else:
            self.heading = discord.ui.TextInput(
                label="Заголовок (необязательно)", required=False, max_length=100, default=ex.get("title") or None,
            )
            self.text = discord.ui.TextInput(
                label="Текст" if kind == "text" else "Вступление (необязательно)",
                style=discord.TextStyle.paragraph, max_length=3900,
                required=(kind == "text"), default=ex.get("content") or None,
            )
            self.add_item(self.heading)
            self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        guild = interaction.guild
        emoji_text = self.emoji.value.strip()
        if emoji_text and resolve_button_emoji(guild, emoji_text) is None:
            await interaction.response.send_message(
                "Эмодзи не распознано. Подойдёт обычный смайлик, имя эмодзи бота или сервера "
                "(например boost или cosimbad) или код <:имя:ID>.", ephemeral=True,
            )
            return
        fields = {"label": self.label.value.strip(), "emoji": emoji_text or None}
        if self.kind == "link":
            url = self.url.value.strip()
            if not url.startswith(("http://", "https://")):
                await interaction.response.send_message("Ссылка должна начинаться с https://", ephemeral=True)
                return
            fields.update(url=url, style=5, title=None, content=None, kind="link")
        else:
            fields.update(url=None, title=self.heading.value.strip() or None,
                          content=self.text.value.strip() or None, kind=self.kind,
                          style=self.style or (self.existing or {}).get("style") or 2)
            if fields["style"] == 5:
                fields["style"] = 2
        await interaction.response.defer(ephemeral=True)
        if self.remove_image:
            fields.update(image=None, image_name=None)
        if self.image is not None:
            try:
                data = await self.image.read()
            except discord.HTTPException:
                await interaction.followup.send("Не удалось скачать картинку.", ephemeral=True)
                return
            ext = re.sub(r"[^A-Za-z0-9.]", "", os.path.splitext(self.image.filename)[1])[:6] or ".png"
            fields.update(image=data, image_name="button" + ext)
        if self.existing:
            db.update_memo_button(self.existing["id"], **fields)
            note = f"Кнопка «{fields['label']}» обновлена."
        else:
            db.add_memo_button(guild.id, **fields)
            note = f"Кнопка «{fields['label']}» добавлена."
        error = await refresh_memo(interaction.client, guild)
        await interaction.followup.send(error or note + " Памятка обновлена.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы кнопки", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


async def button_autocomplete(interaction: discord.Interaction, current: str):
    current = current.casefold()
    return [
        app_commands.Choice(name=f"{b['position']}. {b['label']}"[:100], value=str(b["id"]))
        for b in db.list_memo_buttons(interaction.guild_id) if current in b["label"].casefold()
    ][:25]


def check_image(image: Optional[discord.Attachment], guild: discord.Guild) -> Optional[str]:
    if image is None:
        return None
    if not (image.content_type or "").startswith("image/"):
        return "Нужна картинка (png, jpg, gif, webp)."
    if image.size > min(MAX_BANNER_BYTES, guild.filesize_limit):
        return "Картинка слишком большая (до 8 МБ)."
    return None


class Memo(commands.Cog):
    setting = app_commands.Group(name="info_setting", description="Настройки памятки", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.dirty = set()
        self.ticks = 0

    async def cog_load(self):
        migrate_neutral_v1()
        migrate_buttons()
        migrate_text_v3()
        self.sweep.start()

    async def cog_unload(self):
        self.sweep.cancel()

    # ----- Автообновление -----
    @tasks.loop(seconds=60)
    async def sweep(self):
        self.ticks += 1
        if self.ticks % 15 == 0:
            self.dirty.update(cfg["guild_id"] for cfg in db.list_memos())
        todo, self.dirty = self.dirty, set()
        for guild_id in todo:
            guild = self.bot.get_guild(guild_id)
            cfg = db.get_memo(guild_id)
            if guild and cfg and cfg["message_id"]:
                try:
                    await refresh_memo(self.bot, guild)
                except Exception:
                    logging.exception("Ошибка автообновления памятки")

    @sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_stack_update(self, guild: discord.Guild):
        self.dirty.add(guild.id)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles != after.roles:
            self.dirty.add(after.guild.id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.dirty.add(member.guild.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.dirty.add(member.guild.id)

    # ----- Команды -----
    @app_commands.command(name="info", description="Памятка: отряды, стаки, структура сервера (видно только вам)")
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction):
        guild = interaction.guild
        seed_default_buttons(guild.id)
        payload, upload = build_memo(guild, db.get_memo(guild.id) or {})
        payload = dict(payload)
        payload["flags"] = payload["flags"] | EPHEMERAL
        try:
            await respond_components(interaction.client, interaction, payload, upload)
        except discord.HTTPException:
            logging.exception("Не удалось показать памятку")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Не удалось показать памятку. Сообщите персоналу.", ephemeral=True
                )

    @setting.command(name="post", description="Отправить памятку-обзор сервера в канал")
    @app_commands.describe(channel="Канал для памятки (по умолчанию текущий)")
    async def memo_post(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        guild = interaction.guild
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        old = db.get_memo(guild.id)
        if old and old["channel_id"] and old["message_id"]:
            old_channel = guild.get_channel(old["channel_id"])
            if old_channel:
                try:
                    await (await old_channel.fetch_message(old["message_id"])).delete()
                except discord.HTTPException:
                    pass
        db.set_memo(guild.id, channel_id=target.id, message_id=None)
        error = await refresh_memo(interaction.client, guild, repost=True)
        await interaction.followup.send(error or f"Памятка отправлена в {target.mention}. Дальше она обновляется сама.", ephemeral=True)

    @setting.command(name="text", description="Изменить заголовок, вступление и структуру памятки")
    async def memo_text(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.send_modal(MemoTextModal(interaction.guild, db.get_memo(interaction.guild_id)))

    @setting.command(name="banner", description="Поставить картинку-баннер в памятку (без файла: убрать)")
    @app_commands.describe(image="Картинка для баннера")
    async def memo_banner(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        if image is None:
            db.set_memo(interaction.guild_id, banner=None, banner_name=None)
            note = "Баннер убран."
        else:
            if not (image.content_type or "").startswith("image/"):
                await interaction.response.send_message("Нужна картинка (png, jpg, gif, webp).", ephemeral=True)
                return
            if image.size > min(MAX_BANNER_BYTES, interaction.guild.filesize_limit):
                await interaction.response.send_message("Картинка слишком большая (до 8 МБ).", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                data = await image.read()
            except discord.HTTPException:
                await interaction.followup.send("Не удалось скачать картинку.", ephemeral=True)
                return
            ext = re.sub(r"[^A-Za-z0-9.]", "", os.path.splitext(image.filename)[1])[:6] or ".png"
            db.set_memo(interaction.guild_id, banner=data, banner_name="banner" + ext)
            note = "Баннер сохранён."
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        error = await refresh_memo(interaction.client, interaction.guild)
        await interaction.followup.send(error or note + " Памятка обновлена.", ephemeral=True)

    @setting.command(name="sections", description="Показать/скрыть блоки памятки и переименовать их")
    @app_commands.describe(
        squads="Блок «Отряды»: показать / скрыть",
        stacks="Блок «Стаки»: показать / скрыть",
        structure="Блок «Структура сервера»: показать / скрыть",
        members="Строка «Участников: N»: показать / скрыть",
        squads_title="Новое название блока «Отряды» (- = вернуть стандартное)",
        stacks_title="Новое название блока «Стаки» (- = вернуть стандартное)",
        structure_title="Новое название блока «Структура сервера» (- = вернуть стандартное)",
        buttons_title="Новое название блока с кнопками (- = вернуть стандартное)",
        activities_title="Новое название блока «Чем мы занимаемся» (- = вернуть стандартное)",
        reset="Сбросить всё: блоки автоматически, стандартные названия",
    )
    async def memo_sections(
        self, interaction: discord.Interaction,
        squads: Optional[bool] = None, stacks: Optional[bool] = None,
        structure: Optional[bool] = None, members: Optional[bool] = None,
        squads_title: Optional[app_commands.Range[str, 1, 60]] = None,
        stacks_title: Optional[app_commands.Range[str, 1, 60]] = None,
        structure_title: Optional[app_commands.Range[str, 1, 60]] = None,
        buttons_title: Optional[app_commands.Range[str, 1, 60]] = None,
        activities_title: Optional[app_commands.Range[str, 1, 60]] = None,
        reset: bool = False,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        fields = {}
        if reset:
            fields = {k: None for k in ("show_squads", "show_stacks", "show_structure", "show_members", "squads_title",
                                        "stacks_title", "structure_title", "buttons_title", "activities_title")}
        for name, value in (("show_squads", squads), ("show_stacks", stacks),
                            ("show_structure", structure), ("show_members", members)):
            if value is not None:
                fields[name] = int(value)
        for name, value in (("squads_title", squads_title), ("stacks_title", stacks_title),
                            ("structure_title", structure_title), ("buttons_title", buttons_title),
                            ("activities_title", activities_title)):
            if value is not None:
                value = value.strip()
                fields[name] = None if value in ("", "-") else value
        await interaction.response.defer(ephemeral=True)
        if fields:
            db.set_memo(interaction.guild_id, **fields)
        cfg = db.get_memo(interaction.guild_id) or {}

        def state(key):
            v = cfg.get(key)
            return "авто" if v is None else ("показан" if v else "скрыт")

        summary = (
            "**Блоки памятки:**\n"
            f"• {cfg.get('squads_title') or 'Отряды'}: {state('show_squads')}\n"
            f"• {cfg.get('stacks_title') or 'Стаки'}: {state('show_stacks')}\n"
            f"• {cfg.get('structure_title') or 'Структура сервера'}: {'скрыт' if cfg.get('show_structure') == 0 else 'показан'}\n"
            f"• Строка «Участников»: {'скрыта' if cfg.get('show_members') == 0 else 'показана'}\n"
            f"• {cfg.get('activities_title') or 'Чем мы занимаемся'}: текст меняется в `/info_setting text` (пусто = скрыт)\n"
            f"• {cfg.get('buttons_title') or 'Разделы и ссылки'}: кнопки — `/info_setting button_add / button_edit / button_remove`\n"
            "«авто» = блок виден, только если на сервере есть отряды/стаки."
        )
        error = await refresh_memo(interaction.client, interaction.guild) if cfg.get("channel_id") else None
        await interaction.followup.send(summary + (f"\n\n{error}" if error else ""), ephemeral=True)

    @setting.command(name="links", description="Каналы для подстановки в тексты кнопок («Правила», «Заявки»)")
    @app_commands.describe(
        rules_channel="Канал с правилами (покажется в сообщении кнопки «Правила»)",
        apps_channel="Канал с заявками (покажется в сообщении кнопки «Заявки»)",
    )
    async def memo_setup(
        self,
        interaction: discord.Interaction,
        rules_channel: Optional[discord.abc.GuildChannel] = None,
        apps_channel: Optional[discord.abc.GuildChannel] = None,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        fields = {}
        if rules_channel:
            fields["rules_channel_id"] = rules_channel.id
        if apps_channel:
            fields["apps_channel_id"] = apps_channel.id
        if not fields:
            await interaction.response.send_message("Выберите хотя бы один канал.", ephemeral=True)
            return
        db.set_memo(interaction.guild_id, **fields)
        await interaction.response.send_message(f"Каналы сохранены.", ephemeral=True)

    # ----- Свои кнопки -----
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Нажатие на кнопку памятки: бот отвечает сообщением, которое видит только нажавший."""
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("mb:"):
            return
        try:
            button = db.get_memo_button(int(custom_id[3:]))
        except ValueError:
            button = None
        guild = interaction.guild
        kind = (button or {}).get("kind") or "text"
        if not button or button["guild_id"] != interaction.guild_id or (kind == "text" and not button["content"]):
            await interaction.response.send_message("Эта кнопка больше не доступна.", ephemeral=True)
            return
        title = button["title"] or button["label"]
        cfg = db.get_memo(guild.id) or {}
        upload, image_name = None, None
        sections, extras = [], []
        if kind == "apps":
            sections = parse_sections(button["content"] or APPS_TEXT)
            total, lines = apps_overview(guild)
            extras.append((f"Сейчас в обработке: {total}", lines))
            channel = guild.get_channel(cfg["apps_channel_id"]) if cfg.get("apps_channel_id") else None
            if channel:
                extras.append(("Где подать заявку", channel.mention))
        elif kind == "roles":
            sections = parse_sections(button["content"] or ROLES_TEXT)
            lines = roles_structure_lines(guild, limit=90)
            used = sum(len(h or "") + len(b) for h, b in sections) + 250
            extras.append(("Роли", "\n".join(fit_lines(lines, 3800 - used)) or "Ролей пока нет."))
        else:
            sections = parse_sections(button["content"])
            if button["label"] == "Правила" and cfg.get("rules_channel_id"):
                channel = guild.get_channel(cfg["rules_channel_id"])
                if channel:
                    extras.append(("Полный текст правил", channel.mention))
            if button["image"]:
                image_name = button["image_name"] or "button.png"
                upload = (bytes(button["image"]), image_name)
        payload = build_button_payload(title=title, sections=sections, extra_blocks=extras, image_name=image_name)
        try:
            await respond_components(interaction.client, interaction, payload, upload)
            return
        except discord.HTTPException:
            logging.exception("Не удалось ответить сообщением Components V2, отправляю обычное")
        # запасной вариант: обычное сообщение с рамкой
        text = "\n\n".join((f"**{h}**\n" if h else "") + b for h, b in sections)
        for head, extra in extras:
            text += f"\n\n**{head}**\n{extra}"
        embed = discord.Embed(title=title, description=text[:4096], color=0x2B2D31)
        kwargs = {}
        if upload:
            kwargs["file"] = discord.File(io.BytesIO(upload[0]), filename=upload[1])
            embed.set_image(url=f"attachment://{upload[1]}")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=NO_PING, **kwargs)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_PING, **kwargs)

    @setting.command(name="role_desc", description="Описание роли для кнопки «Структура ролей»")
    @app_commands.describe(
        role="Роль",
        text="Описание (пустая строка «-» вернёт стандартное)",
        hidden="Скрыть роль из списка (да/нет)",
    )
    async def role_desc(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        text: Optional[str] = None,
        hidden: Optional[bool] = None,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        if text is None and hidden is None:
            current = db.get_role_infos(interaction.guild_id).get(role.id, {})
            await interaction.response.send_message(
                f"{role.mention}: {current.get('description') or 'стандартное описание'}"
                + (" (скрыта)" if current.get("hidden") else ""),
                ephemeral=True, allowed_mentions=NO_PING,
            )
            return
        if text is not None:
            db.set_role_description(interaction.guild_id, role.id, None if text.strip() in ("", "-") else text.strip()[:200])
        if hidden is not None:
            db.set_role_hidden(interaction.guild_id, role.id, hidden)
        await interaction.response.send_message(f"Роль {role.mention} обновлена.", ephemeral=True, allowed_mentions=NO_PING)

    @setting.command(name="button_add", description="Добавить свою кнопку в памятку (с текстом или ссылкой)")
    @app_commands.describe(kind="Что делает кнопка", style="Цвет кнопки", image="Картинка к тексту кнопки")
    @app_commands.choices(kind=KIND_CHOICES, style=STYLE_CHOICES)
    async def button_add(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        style: Optional[app_commands.Choice[int]] = None,
        image: Optional[discord.Attachment] = None,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        if len(db.list_memo_buttons(interaction.guild_id)) >= MAX_CUSTOM_BUTTONS:
            await interaction.response.send_message(
                f"Можно добавить до {MAX_CUSTOM_BUTTONS} своих кнопок. Удалите лишнюю: /info_setting button_remove.", ephemeral=True
            )
            return
        error = check_image(image, interaction.guild) if kind.value == "text" else None
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(
            ButtonModal(None, kind.value, style.value if style else None, image if kind.value == "text" else None, False)
        )

    @setting.command(name="button_edit", description="Изменить свою кнопку памятки")
    @app_commands.describe(button="Кнопка", style="Новый цвет", image="Новая картинка", remove_image="Убрать картинку")
    @app_commands.choices(style=STYLE_CHOICES)
    @app_commands.autocomplete(button=button_autocomplete)
    async def button_edit(
        self,
        interaction: discord.Interaction,
        button: str,
        style: Optional[app_commands.Choice[int]] = None,
        image: Optional[discord.Attachment] = None,
        remove_image: bool = False,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        existing = db.get_memo_button(int(button)) if button.isdigit() else None
        if not existing or existing["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(f"Такой кнопки нет.", ephemeral=True)
            return
        kind = "link" if existing["url"] else (existing.get("kind") or "text")
        error = check_image(image, interaction.guild) if kind == "text" else None
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_modal(
            ButtonModal(existing, kind, style.value if style else None, image if kind == "text" else None, remove_image)
        )

    @setting.command(name="button_remove", description="Удалить свою кнопку памятки")
    @app_commands.describe(button="Кнопка")
    @app_commands.autocomplete(button=button_autocomplete)
    async def button_remove(self, interaction: discord.Interaction, button: str):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        existing = db.get_memo_button(int(button)) if button.isdigit() else None
        if not existing or existing["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(f"Такой кнопки нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        db.delete_memo_button(existing["id"])
        error = await refresh_memo(interaction.client, interaction.guild)
        await interaction.followup.send(error or f"Кнопка «{existing['label']}» удалена.", ephemeral=True)

    @setting.command(name="button_move", description="Переставить свою кнопку памятки")
    @app_commands.describe(button="Кнопка", position="Новое место (1 = первая)")
    @app_commands.autocomplete(button=button_autocomplete)
    async def button_move(self, interaction: discord.Interaction, button: str, position: app_commands.Range[int, 1, 10]):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        existing = db.get_memo_button(int(button)) if button.isdigit() else None
        if not existing or existing["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(f"Такой кнопки нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        db.move_memo_button(interaction.guild_id, existing["id"], position)
        error = await refresh_memo(interaction.client, interaction.guild)
        await interaction.followup.send(error or f"Кнопка «{existing['label']}» перемещена.", ephemeral=True)

    @setting.command(name="button_list", description="Список своих кнопок памятки")
    async def button_list(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        buttons = db.list_memo_buttons(interaction.guild_id)
        lines = [
            f"{b['position']}. {(b['emoji'] + ' ') if b['emoji'] else ''}**{b['label']}** · "
            + ("ссылка" if b["url"] else f"текст ({len(b['content'] or '')} симв.)" + (", есть картинка" if b["image"] else ""))
            for b in buttons
        ]
        await interaction.response.send_message(
            "\n".join(lines) or "Своих кнопок пока нет. Добавьте: /info_setting button_add.", ephemeral=True, allowed_mentions=NO_PING
        )

    @setting.command(name="refresh", description="Обновить памятку сейчас")
    async def memo_refresh(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        error = await refresh_memo(interaction.client, interaction.guild)
        await interaction.followup.send(error or "Памятка обновлена.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Memo(bot))
