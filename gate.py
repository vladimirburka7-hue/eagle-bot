"""Верификация с выбором игр и партнёры.

Верификация: новичок видит только канал верификации. Кнопка «Пройти верификацию» выдаёт роль доступа
и сразу открывает меню игр (Dota 2, Hearts of Iron, CS…). Каждая игра = роль, которая открывает свою
категорию каналов. Поменять игры можно в любой момент: кнопка «Мои игры» или команда /games.

Партнёры: витрина с меню (/partners или сообщение в канале), заявка на партнёрство кнопкой,
карточка заявки в канал персонала с кнопками «Принять / Отклонить», роль партнёра.

Команды персонала: /setting verify (setup, game_add, game_remove, games, panel, access)
                   /setting partners (setup, add, remove, post)
"""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from content import IS_COMPONENTS_V2, EPHEMERAL, _btn, _code, _gap, _line, _t, edit_components, post_components, respond_components
from emojis import EMOJI, butterfly, emoji_to_button, icon
from utils import can_manage

NO_PING = discord.AllowedMentions.none()
ACCENT = 0x5865F2

# ---------- База ----------
db.conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS verify_settings (
        guild_id INTEGER PRIMARY KEY,
        verified_role_id INTEGER,
        channel_id INTEGER, message_id INTEGER,
        title TEXT, text TEXT
    );
    CREATE TABLE IF NOT EXISTS verify_games (
        guild_id INTEGER, role_id INTEGER, name TEXT, emoji TEXT, description TEXT, position INTEGER,
        PRIMARY KEY (guild_id, role_id)
    );
    CREATE TABLE IF NOT EXISTS partner_settings (
        guild_id INTEGER PRIMARY KEY,
        apps_channel_id INTEGER, partner_role_id INTEGER,
        channel_id INTEGER, message_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS partners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, name TEXT NOT NULL, link TEXT, description TEXT, image_url TEXT,
        emoji TEXT, user_id INTEGER, created_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS partner_apps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        name TEXT, link TEXT, description TEXT, extra TEXT,
        status TEXT NOT NULL DEFAULT 'open', decided_by INTEGER, created_at INTEGER
    );
    """
)
db.conn.commit()


def q(sql, args=()):
    return [dict(r) for r in db.conn.execute(sql, args).fetchall()]


def q1(sql, args=()):
    row = db.conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def ex(sql, args=()):
    cur = db.conn.execute(sql, args)
    db.conn.commit()
    return cur


def upsert(table, guild_id, **fields):
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    ex(f"INSERT INTO {table} (guild_id, {names}) VALUES (?, {marks}) ON CONFLICT(guild_id) DO UPDATE SET {updates}",
       (guild_id, *fields.values()))


def verify_settings(gid):
    return q1("SELECT * FROM verify_settings WHERE guild_id = ?", (gid,)) or {}


def games_of(guild: discord.Guild):
    """Игры сервера (только те, чьи роли ещё существуют)."""
    rows = q("SELECT * FROM verify_games WHERE guild_id = ? ORDER BY position, name", (guild.id,))
    return [r for r in rows if guild.get_role(r["role_id"])]


def partner_settings(gid):
    return q1("SELECT * FROM partner_settings WHERE guild_id = ?", (gid,)) or {}


def partners_of(gid):
    return q("SELECT * FROM partners WHERE guild_id = ? ORDER BY id", (gid,))


# ---------- Сообщения ----------
DEFAULT_TITLE = "Добро пожаловать в Mountain Eagles"
DEFAULT_TEXT = "Нажми «Войти» и выбери свои игры, тогда увидишь только нужные каналы."


def verify_panel_payload(guild: discord.Guild):
    st = verify_settings(guild.id)
    BF = EMOJI["title"]
    games = games_of(guild)
    items = [_t(f"## {BF} {st.get('title') or DEFAULT_TITLE}"), _t(st.get("text") or DEFAULT_TEXT)]
    if games:
        lines = []
        for g in games:
            em = (g["emoji"] + " ") if g["emoji"] else ""
            lines.append(f"{em}**{g['name']}**" + (f": {g['description']}" if g["description"] else ""))
        items += [_t("\n".join(lines))]
    items += [{"type": 1, "components": [
        _btn("Войти", "vf:go", 3, emoji="tick"),
        _btn("Мои игры", "vf:games", emoji="host"),
    ]}]
    return {"flags": IS_COMPONENTS_V2, "components": [{"type": 17, "accent_color": ACCENT, "components": items}],
            "allowed_mentions": {"parse": []}}


def games_menu_payload(member: discord.Member, first_time: bool):
    BF = EMOJI["title"]
    games = games_of(member.guild)[:25]
    have = {r.id for r in member.roles}
    head = f"{EMOJI['tick']} Доступ открыт!" if first_time else f"{EMOJI['host']} Мои игры"
    items = [_t(f"## {head}"), _t("Отметь свои игры, каналы остальных скроются.")]
    if games:
        options = []
        for g in games:
            opt = {"label": g["name"][:100], "value": str(g["role_id"]), "default": g["role_id"] in have}
            if g["description"]:
                opt["description"] = g["description"][:100]
            em = emoji_to_button(g["emoji"])
            if em:
                opt["emoji"] = em
            options.append(opt)
        items += [_gap(), {"type": 1, "components": [{
            "type": 3, "custom_id": "vf:pick", "placeholder": "Выбери игры", "min_values": 0,
            "max_values": len(options), "options": options}]}]
    else:
        items += [_t("Игры ещё не настроены.")]
    return {"flags": IS_COMPONENTS_V2 | EPHEMERAL, "components": [{"type": 17, "accent_color": ACCENT, "components": items}],
            "allowed_mentions": {"parse": []}}


def partners_payload(guild: discord.Guild, ephemeral: bool):
    BF = EMOJI["title"]
    rows = partners_of(guild.id)
    items = [_t(f"## {EMOJI['partner']} Партнёры")]
    if rows:
        lines = []
        for p in rows:
            em = (p["emoji"] + " ") if p["emoji"] else ""
            name = f"[{p['name']}]({p['link']})" if p["link"] else f"**{p['name']}**"
            lines.append(f"{em}{name}")
        items.append(_t("\n".join(lines)[:3500]))
        options = []
        for p in rows[:25]:
            opt = {"label": p["name"][:100], "value": str(p["id"])}
            if p["description"]:
                opt["description"] = p["description"][:100]
            em = emoji_to_button(p["emoji"])
            if em:
                opt["emoji"] = em
            options.append(opt)
        items += [_gap(), {"type": 1, "components": [{
            "type": 3, "custom_id": "pt:view", "placeholder": "Подробнее о партнёре", "options": options}]}]
    else:
        items.append(_t("Пока нет. Станьте первыми!"))
    items += [{"type": 1, "components": [
        _btn("Стать партнёром", "pt:apply", 1, emoji="partner"),
    ]}]
    flags = IS_COMPONENTS_V2 | (EPHEMERAL if ephemeral else 0)
    return {"flags": flags, "components": [{"type": 17, "accent_color": ACCENT, "components": items}],
            "allowed_mentions": {"parse": []}}


def partner_card_payload(p: dict):
    BF = EMOJI["title"]
    em = (p["emoji"] + " ") if p["emoji"] else ""
    items = [_t(f"## {em}{p['name']}"), _t(_code((p["description"] or "Без описания").replace("```", "ʼʼʼ")))]
    if p.get("user_id"):
        items += [_t(f"<@{p['user_id']}>")]
    if p["image_url"]:
        items += [_gap(), {"type": 12, "items": [{"media": {"url": p["image_url"]}}]}]
    if p["link"]:
        items += [{"type": 1, "components": [{"type": 2, "style": 5, "label": "Перейти", "url": p["link"]}]}]
    return {"flags": IS_COMPONENTS_V2 | EPHEMERAL, "components": [{"type": 17, "accent_color": ACCENT, "components": items}],
            "allowed_mentions": {"parse": []}}


def app_card_payload(app: dict):
    BF = EMOJI["title"]
    colors = {"open": 0xFFB84D, "accepted": 0x58C58A, "declined": 0xE2584D}
    status = {"open": f"{EMOJI['wait']} Ждёт ответа", "accepted": f"{EMOJI['ok']} Принята, <@{app['decided_by']}>",
              "declined": f"{EMOJI['no']} Отклонена, <@{app['decided_by']}>"}[app["status"]]
    text = (f"## {EMOJI['partner']} Партнёрство №{app['id']:02d}\n"
            f"<@{app['user_id']}>, **{app['name']}**\n{status}" + (f"\n{app['link']}" if app['link'] else ""))
    items = [_t(text), _line(), _t(_code((app["description"] or "—").replace("```", "ʼʼʼ")))]
    if app["extra"]:
        items += [_t("**Предлагает**\n" + _code(app["extra"].replace("```", "ʼʼʼ")))]
    if app["status"] == "open":
        items += [{"type": 1, "components": [
            _btn("Принять", f"pt:ok:{app['id']}", emoji="ok"),
            _btn("Отклонить", f"pt:no:{app['id']}", emoji="no"),
        ]}]
    return {"flags": IS_COMPONENTS_V2, "components": [{"type": 17, "accent_color": colors[app["status"]], "components": items}],
            "allowed_mentions": {"parse": []}}


async def refresh_partners(bot, guild: discord.Guild):
    st = partner_settings(guild.id)
    if st.get("channel_id") and st.get("message_id"):
        try:
            await edit_components(bot, st["channel_id"], st["message_id"], partners_payload(guild, False))
        except discord.HTTPException:
            logging.warning("Не удалось обновить витрину партнёров")


async def refresh_verify(bot, guild: discord.Guild):
    st = verify_settings(guild.id)
    if st.get("channel_id") and st.get("message_id"):
        try:
            await edit_components(bot, st["channel_id"], st["message_id"], verify_panel_payload(guild))
        except discord.HTTPException:
            logging.warning("Не удалось обновить панель верификации")


async def reply(interaction: discord.Interaction, payload: dict, fallback: str):
    try:
        await respond_components(interaction.client, interaction, payload)
    except discord.HTTPException:
        logging.exception("Не удалось ответить сообщением Components V2")
        if not interaction.response.is_done():
            await interaction.response.send_message(fallback[:1900], ephemeral=True)


def role_error(e: Exception) -> str:
    return ("Боту не хватает прав: дайте ему право «Управление ролями» и поставьте его роль выше ролей игр, "
            f"доступа и партнёра. ({e})")


# ---------- Формы ----------
class PartnerApplyModal(discord.ui.Modal, title="Заявка на партнёрство"):
    def __init__(self):
        super().__init__(timeout=600)
        self.name = discord.ui.TextInput(label="Название сервера / клана / проекта", max_length=80)
        self.link = discord.ui.TextInput(label="Ссылка (приглашение, сайт)", required=False, max_length=200)
        self.description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, max_length=800)
        self.extra = discord.ui.TextInput(label="Что предлагаете (обмен рекламой и т.п.)", style=discord.TextStyle.paragraph,
                                          required=False, max_length=500)
        for item in (self.name, self.link, self.description, self.extra):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        st = partner_settings(guild.id)
        channel = guild.get_channel(st.get("apps_channel_id") or 0)
        if channel is None:
            await interaction.response.send_message("Заявки на партнёрство пока не настроены.", ephemeral=True)
            return
        cur = ex("INSERT INTO partner_apps (guild_id, user_id, name, link, description, extra, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (guild.id, interaction.user.id, self.name.value.strip(), self.link.value.strip(),
                  self.description.value.strip(), self.extra.value.strip(), int(time.time())))
        app = q1("SELECT * FROM partner_apps WHERE id = ?", (cur.lastrowid,))
        try:
            await post_components(interaction.client, channel, app_card_payload(app))
        except discord.HTTPException:
            logging.exception("Не удалось отправить заявку на партнёрство")
            await interaction.response.send_message("Не удалось отправить заявку, сообщите персоналу.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{EMOJI['tick']} Заявка №{app['id']:02d} отправлена. Ответ придёт в личку.",
            ephemeral=True)


class PartnerAddModal(discord.ui.Modal, title="Новый партнёр"):
    def __init__(self, emoji: str, image_url: str, user_id: Optional[int]):
        super().__init__(timeout=600)
        self.emoji, self.image_url, self.user_id = emoji, image_url, user_id
        self.name = discord.ui.TextInput(label="Название", max_length=80)
        self.link = discord.ui.TextInput(label="Ссылка", required=False, max_length=200)
        self.description = discord.ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False,
                                                max_length=800)
        for item in (self.name, self.link, self.description):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        link = self.link.value.strip()
        if link and not link.startswith(("http://", "https://")):
            link = "https://" + link
        ex("INSERT INTO partners (guild_id, name, link, description, image_url, emoji, user_id, created_at) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           (interaction.guild_id, self.name.value.strip(), link, self.description.value.strip(), self.image_url,
            self.emoji, self.user_id, int(time.time())))
        await interaction.response.send_message(f"Партнёр «{self.name.value.strip()}» добавлен.", ephemeral=True)
        await refresh_partners(interaction.client, interaction.guild)


# ---------- Модуль ----------
class Gate(commands.Cog):
    verify = app_commands.Group(name="verify_setting", description="Верификация и выбор игр (для персонала)", guild_only=True)
    partners_set = app_commands.Group(name="partners_setting", description="Партнёры (для персонала)", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        root = interaction.command.root_parent if interaction.command and interaction.command.root_parent else interaction.command
        if root and root.name in ("verify_setting", "partners_setting") and not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return False
        return True

    # ----- кнопки и меню -----
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or interaction.guild is None:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith(("vf:", "pt:")):
            return
        try:
            await self.handle(interaction, cid)
        except Exception:
            logging.exception("Ошибка кнопки %s", cid)
            if not interaction.response.is_done():
                await interaction.response.send_message("Что-то пошло не так, попробуйте ещё раз.", ephemeral=True)

    async def handle(self, interaction: discord.Interaction, cid: str):
        guild, member = interaction.guild, interaction.user
        if cid == "vf:go":
            st = verify_settings(guild.id)
            role = guild.get_role(st.get("verified_role_id") or 0)
            if role is None:
                await interaction.response.send_message("Верификация не настроена: /setting verify setup", ephemeral=True)
                return
            first = role not in member.roles
            if first:
                try:
                    await member.add_roles(role, reason="Верификация")
                except discord.HTTPException as e:
                    await interaction.response.send_message(role_error(e), ephemeral=True)
                    return
            await reply(interaction, games_menu_payload(member, first), "Доступ открыт. Игры можно выбрать командой /games.")
        elif cid == "vf:games":
            role = guild.get_role(verify_settings(guild.id).get("verified_role_id") or 0)
            if role and role not in member.roles:
                await interaction.response.send_message("Сначала нажмите «Пройти верификацию».", ephemeral=True)
                return
            await reply(interaction, games_menu_payload(member, False), "Выберите игры командой /games.")
        elif cid == "vf:pick":
            await self.apply_games(interaction, interaction.data.get("values", []))
        elif cid == "pt:view":
            values = interaction.data.get("values", [])
            p = q1("SELECT * FROM partners WHERE id = ? AND guild_id = ?", (int(values[0]), guild.id)) if values else None
            if p is None:
                await interaction.response.send_message("Этого партнёра больше нет в списке.", ephemeral=True)
                return
            await reply(interaction, partner_card_payload(p), f"{p['name']}\n{p['link'] or ''}\n{p['description'] or ''}")
        elif cid == "pt:apply":
            if not partner_settings(guild.id).get("apps_channel_id"):
                await interaction.response.send_message("Заявки на партнёрство пока не принимаются.", ephemeral=True)
                return
            await interaction.response.send_modal(PartnerApplyModal())
        elif cid.startswith(("pt:ok:", "pt:no:")):
            await self.decide(interaction, int(cid.split(":")[2]), cid.startswith("pt:ok:"))

    async def apply_games(self, interaction: discord.Interaction, values):
        member, guild = interaction.user, interaction.guild
        chosen = {int(v) for v in values}
        game_roles = [guild.get_role(g["role_id"]) for g in games_of(guild)]
        add = [r for r in game_roles if r and r.id in chosen and r not in member.roles]
        remove = [r for r in game_roles if r and r.id not in chosen and r in member.roles]
        try:
            if add:
                await member.add_roles(*add, reason="Выбор игр")
            if remove:
                await member.remove_roles(*remove, reason="Выбор игр")
        except discord.HTTPException as e:
            await interaction.response.send_message(role_error(e), ephemeral=True)
            return
        names = [r.name for r in game_roles if r and r.id in chosen]
        text = ("Готово! Ваши игры: " + ", ".join(names)) if names else "Готово. Игры не выбраны: открыты только общие каналы."
        await interaction.response.send_message(f"{text}", ephemeral=True)

    async def decide(self, interaction: discord.Interaction, app_id: int, accept: bool):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        guild = interaction.guild
        app = q1("SELECT * FROM partner_apps WHERE id = ? AND guild_id = ?", (app_id, guild.id))
        if app is None or app["status"] != "open":
            await interaction.response.send_message("Эта заявка уже рассмотрена.", ephemeral=True)
            return
        ex("UPDATE partner_apps SET status = ?, decided_by = ? WHERE id = ?",
           ("accepted" if accept else "declined", interaction.user.id, app_id))
        app = q1("SELECT * FROM partner_apps WHERE id = ?", (app_id,))
        await interaction.response.defer()
        try:
            await edit_components(self.bot, interaction.channel_id, interaction.message.id, app_card_payload(app))
        except discord.HTTPException:
            logging.warning("Не удалось обновить карточку заявки")
        member = guild.get_member(app["user_id"])
        notes = []
        if accept:
            link = app["link"]
            if link and not link.startswith(("http://", "https://")):
                link = "https://" + link
            ex("INSERT INTO partners (guild_id, name, link, description, user_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (guild.id, app["name"], link, app["description"], app["user_id"], int(time.time())))
            await refresh_partners(self.bot, guild)
            role = guild.get_role(partner_settings(guild.id).get("partner_role_id") or 0)
            if role and member:
                try:
                    await member.add_roles(role, reason="Партнёрство принято")
                except discord.HTTPException as e:
                    notes.append(role_error(e))
        if member:
            text = (f"Ваша заявка на партнёрство с сервером **{guild.name}** "
                    + ("принята! Добро пожаловать в партнёры." if accept else "отклонена."))
            try:
                await member.send(text)
            except discord.HTTPException:
                notes.append("Не удалось написать заявителю в личные сообщения.")
        if notes:
            await interaction.followup.send("\n".join(notes), ephemeral=True)

    # ----- для всех -----
    @app_commands.command(name="games", description="Выбрать игры: какие каналы вам видны")
    @app_commands.guild_only()
    async def games_cmd(self, interaction: discord.Interaction):
        await reply(interaction, games_menu_payload(interaction.user, False), "Игры пока не настроены.")

    @app_commands.command(name="partners", description="Наши партнёры и заявка на партнёрство")
    @app_commands.guild_only()
    async def partners_cmd(self, interaction: discord.Interaction):
        await reply(interaction, partners_payload(interaction.guild, True), "Партнёров пока нет.")

    # ----- верификация -----
    @verify.command(name="setup", description="Роль доступа и канал с кнопкой верификации")
    @app_commands.describe(verified_role="Роль, которая открывает сервер после нажатия кнопки",
                           channel="Канал верификации (по умолчанию текущий)",
                           title="Заголовок сообщения", text="Текст сообщения")
    async def verify_setup(self, interaction: discord.Interaction, verified_role: discord.Role,
                           channel: Optional[discord.TextChannel] = None, title: Optional[str] = None,
                           text: Optional[str] = None):
        guild = interaction.guild
        if verified_role >= guild.me.top_role or verified_role.managed or verified_role.is_default():
            await interaction.response.send_message(
                "Эту роль бот выдать не сможет: выберите обычную роль ниже роли бота.", ephemeral=True)
            return
        channel = channel or interaction.channel
        st = verify_settings(guild.id)
        upsert("verify_settings", guild.id, verified_role_id=verified_role.id,
               title=title or st.get("title"), text=text or st.get("text"))
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await post_components(self.bot, channel, verify_panel_payload(guild))
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить сообщение в {channel.mention}: {e}", ephemeral=True)
            return
        upsert("verify_settings", guild.id, channel_id=channel.id, message_id=int(msg["id"]))
        tips = []
        if not guild.me.guild_permissions.manage_roles:
            tips.append("Дайте боту право «Управление ролями».")
        tips.append("Дальше: /setting verify game_add для каждой игры, затем /setting verify access, чтобы скрыть категории.")
        await interaction.followup.send(f"Панель верификации отправлена в {channel.mention}.\n" + "\n".join(tips), ephemeral=True)

    @verify.command(name="game_add", description="Добавить игру/направление в меню (или изменить)")
    @app_commands.describe(role="Роль игры (открывает её категорию)", name="Название в меню (по умолчанию имя роли)",
                           emoji="Смайлик или эмодзи сервера/бота", description="Короткое описание")
    async def game_add(self, interaction: discord.Interaction, role: discord.Role, name: Optional[str] = None,
                       emoji: Optional[str] = None, description: Optional[str] = None):
        guild = interaction.guild
        if role >= guild.me.top_role or role.managed or role.is_default():
            await interaction.response.send_message("Эту роль бот выдать не сможет: она выше роли бота.", ephemeral=True)
            return
        if emoji and emoji_to_button(emoji) is None:
            await interaction.response.send_message("Не понял эмодзи: вставьте обычный смайлик или эмодзи сервера.", ephemeral=True)
            return
        if len(games_of(guild)) >= 25 and not q1("SELECT 1 FROM verify_games WHERE guild_id = ? AND role_id = ?", (guild.id, role.id)):
            await interaction.response.send_message("В меню максимум 25 игр.", ephemeral=True)
            return
        pos = (q1("SELECT MAX(position) AS m FROM verify_games WHERE guild_id = ?", (guild.id,)) or {}).get("m") or 0
        ex("INSERT INTO verify_games (guild_id, role_id, name, emoji, description, position) VALUES (?, ?, ?, ?, ?, ?) "
           "ON CONFLICT(guild_id, role_id) DO UPDATE SET name = excluded.name, emoji = excluded.emoji, "
           "description = excluded.description",
           (guild.id, role.id, (name or role.name)[:80], (emoji or "").strip(), (description or "")[:100], pos + 1))
        await interaction.response.send_message(f"Игра «{name or role.name}» в меню.", ephemeral=True)
        await refresh_verify(self.bot, guild)

    @verify.command(name="game_remove", description="Убрать игру из меню (роль не удаляется)")
    @app_commands.describe(role="Роль игры")
    async def game_remove(self, interaction: discord.Interaction, role: discord.Role):
        cur = ex("DELETE FROM verify_games WHERE guild_id = ? AND role_id = ?", (interaction.guild_id, role.id))
        await interaction.response.send_message("Убрано." if cur.rowcount else "Этой игры нет в меню.", ephemeral=True)
        await refresh_verify(self.bot, interaction.guild)

    @verify.command(name="games", description="Показать настройки верификации и список игр")
    async def verify_games_list(self, interaction: discord.Interaction):
        guild = interaction.guild
        st = verify_settings(guild.id)
        role = guild.get_role(st.get("verified_role_id") or 0)
        lines = [f"**Роль доступа:** {role.mention if role else 'не задана'}",
                 "**Канал:** " + (f"<#{st['channel_id']}>" if st.get("channel_id") else "не задан"), "", "**Игры:**"]
        for g in games_of(guild):
            r = guild.get_role(g["role_id"])
            lines.append(f"{g['emoji']} {g['name']} → {r.mention} ({len(r.members)} чел.)")
        if len(lines) == 4:
            lines.append("пока нет: /setting verify game_add")
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True, allowed_mentions=NO_PING)

    @verify.command(name="panel", description="Отправить панель верификации заново")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    async def verify_panel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        channel = channel or interaction.channel
        if not verify_settings(interaction.guild_id).get("verified_role_id"):
            await interaction.response.send_message("Сначала /setting verify setup", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await post_components(self.bot, channel, verify_panel_payload(interaction.guild))
        upsert("verify_settings", interaction.guild_id, channel_id=channel.id, message_id=int(msg["id"]))
        await interaction.followup.send(f"Отправлено в {channel.mention}.", ephemeral=True)

    @verify.command(name="access", description="Категорию видит только эта роль (повторно: открыть для всех)")
    @app_commands.describe(category="Категория каналов", role="Кто её видит (роль игры, отряда или доступа)")
    async def verify_access(self, interaction: discord.Interaction, category: discord.CategoryChannel, role: discord.Role):
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_roles or not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message("Боту нужны права «Управление каналами» и «Управление ролями».", ephemeral=True)
            return
        everyone = category.overwrites_for(guild.default_role)
        hidden = everyone.view_channel is False and category.overwrites_for(role).view_channel is True
        await interaction.response.defer(ephemeral=True)
        try:
            if hidden:
                await category.set_permissions(guild.default_role, view_channel=None, reason="Открыть категорию")
                await category.set_permissions(role, overwrite=None, reason="Открыть категорию")
                result = f"Категория **{category.name}** снова видна всем."
            else:
                await category.set_permissions(guild.me, view_channel=True, manage_channels=True, connect=True,
                                               move_members=True, reason="Бот видит категорию")
                await category.set_permissions(guild.default_role, view_channel=False, reason="Скрыть категорию")
                await category.set_permissions(role, view_channel=True, reason="Категория для роли")
                result = f"Категорию **{category.name}** теперь видит только {role.mention}."
            not_synced = [c.mention for c in category.channels if not c.permissions_synced]
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не получилось изменить права: {e}", ephemeral=True)
            return
        if not_synced:
            result += ("\nЭти каналы не синхронизированы с категорией и сохранили свои права: " + ", ".join(not_synced[:15])
                       + ". Откройте канал → «Права доступа» → «Синхронизировать».")
        await interaction.followup.send(result, ephemeral=True, allowed_mentions=NO_PING)

    # ----- партнёры -----
    @partners_set.command(name="setup", description="Канал заявок на партнёрство и роль партнёра")
    @app_commands.describe(apps_channel="Канал персонала, куда приходят заявки", partner_role="Роль для партнёров")
    async def partners_setup(self, interaction: discord.Interaction, apps_channel: discord.TextChannel,
                             partner_role: Optional[discord.Role] = None):
        upsert("partner_settings", interaction.guild_id, apps_channel_id=apps_channel.id,
               partner_role_id=partner_role.id if partner_role else None)
        await interaction.response.send_message(
            f"Заявки будут приходить в {apps_channel.mention}"
            + (f", партнёрам выдаётся {partner_role.mention}." if partner_role else ".")
            + "\nВитрина в канал: /setting partners post", ephemeral=True, allowed_mentions=NO_PING)

    @partners_set.command(name="add", description="Добавить партнёра вручную (откроется форма)")
    @app_commands.describe(emoji="Смайлик (необязательно)", image_url="Ссылка на баннер (необязательно)",
                           representative="Представитель партнёра (получит роль партнёра)")
    async def partners_add(self, interaction: discord.Interaction, emoji: Optional[str] = None,
                           image_url: Optional[str] = None, representative: Optional[discord.Member] = None):
        if emoji and emoji_to_button(emoji) is None:
            await interaction.response.send_message("Не понял эмодзи.", ephemeral=True)
            return
        if image_url and not image_url.startswith(("http://", "https://")):
            await interaction.response.send_message("Ссылка на баннер должна начинаться с https://", ephemeral=True)
            return
        if representative:
            role = interaction.guild.get_role(partner_settings(interaction.guild_id).get("partner_role_id") or 0)
            if role:
                try:
                    await representative.add_roles(role, reason="Партнёр")
                except discord.HTTPException:
                    pass
        await interaction.response.send_modal(PartnerAddModal((emoji or "").strip(), image_url or "",
                                                              representative.id if representative else None))

    async def partner_names(self, interaction: discord.Interaction, current: str):
        rows = partners_of(interaction.guild_id)
        return [app_commands.Choice(name=p["name"][:100], value=str(p["id"]))
                for p in rows if current.lower() in p["name"].lower()][:25]

    @partners_set.command(name="remove", description="Убрать партнёра из витрины")
    @app_commands.describe(partner="Кого убрать")
    @app_commands.autocomplete(partner=partner_names)
    async def partners_remove(self, interaction: discord.Interaction, partner: str):
        p = q1("SELECT * FROM partners WHERE guild_id = ? AND id = ?", (interaction.guild_id, int(partner))) if partner.isdigit() else None
        if p is None:
            await interaction.response.send_message("Не нашёл такого партнёра.", ephemeral=True)
            return
        ex("DELETE FROM partners WHERE id = ?", (p["id"],))
        await interaction.response.send_message(f"«{p['name']}» убран из партнёров. Роль у представителя снимите вручную, если нужно.",
                                                ephemeral=True)
        await refresh_partners(self.bot, interaction.guild)

    @partners_set.command(name="post", description="Отправить витрину партнёров в канал (обновляется сама)")
    @app_commands.describe(channel="Канал (по умолчанию текущий)")
    async def partners_post(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        channel = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await post_components(self.bot, channel, partners_payload(interaction.guild, False))
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось отправить: {e}", ephemeral=True)
            return
        upsert("partner_settings", interaction.guild_id, channel_id=channel.id, message_id=int(msg["id"]))
        await interaction.followup.send(f"Витрина отправлена в {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Gate(bot))
