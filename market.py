"""Рынок: объявления об услугах с кнопками «Откликнуться», «Отклики», «Закрыть»."""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from emojis import EMOJI
from content import (
    EPHEMERAL, build_button_payload, build_market_guide_payload, build_market_payload, edit_components, post_components,
    respond_components,
)
from utils import can_manage

NO_PING = discord.AllowedMentions.none()
BUMP_COOLDOWN = 12 * 3600
DEFAULT_RULES = (
    "1. До {max_active} открытых объявлений на человека\n"
    "2. Объявление актуально {days} дн., потом закрывается само\n"
    "3. «Поднять» объявление можно раз в 12 часов\n"
    "4. Крупные сделки лучше проводить через гаранта\n"
    "5. Обман, спам и реклама сторонних ресурсов запрещены"
)
KIND_CHOICES = [
    app_commands.Choice(name="Предлагаю услугу", value="offer"),
    app_commands.Choice(name="Ищу услугу", value="request"),
]


GUARANTOR_NAMES = {"гарант", "гаранты", "guarantor", "guarantee"}


def find_guarantor_role(guild: discord.Guild, settings: Optional[dict]):
    """Роль гарантов: выбранная в настройках или найденная по названию «Гарант»."""
    role_id = (settings or {}).get("guarantor_role_id")
    role = guild.get_role(role_id) if role_id else None
    if role:
        return role
    for candidate in guild.roles:
        if " ".join(candidate.name.replace("・", " ").strip().casefold().split()) in GUARANTOR_NAMES:
            return candidate
    return None


def has_guarantors(guild: discord.Guild, settings: Optional[dict]) -> bool:
    channel_id = (settings or {}).get("guarantor_channel_id")
    return bool(find_guarantor_role(guild, settings) or (channel_id and guild.get_channel(channel_id)))


def guarantors_payload(guild: discord.Guild) -> dict:
    """Сообщение «только для вас»: кто сейчас гарант и где с ним связаться."""
    settings = db.get_market_settings(guild.id) or {}
    role = find_guarantor_role(guild, settings)
    members = [m for m in role.members if not m.bot] if role else []
    extras = [(f"Гаранты ({len(members)})", " ".join(m.mention for m in members[:25]) or "Сейчас никого нет.")]
    channel_id = settings.get("guarantor_channel_id")
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel:
        extras.append(("Канал гаранта", channel.mention))
    sections = [(None, "Сделка на крупную сумму? Позови гаранта, он проследит, чтобы всё прошло честно.")]
    return build_button_payload(title="Гаранты", sections=sections, extra_blocks=extras)


def listing_payload(guild: discord.Guild, listing: dict) -> dict:
    settings = db.get_market_settings(guild.id) or {}
    return build_market_payload(
        kind=listing["kind"], title=listing["title"], user_id=listing["user_id"],
        price=listing["price"], description=listing["description"], notes=listing["notes"],
        contact=listing["contact"], avatar_url=listing["avatar_url"], expires_at=listing["expires_at"],
        status=listing["status"], closed_reason=listing["closed_reason"],
        responses=len(db.list_market_responses(listing["id"])), guarantors=has_guarantors(guild, settings),
        number=listing["id"],
    )


async def refresh_listing(bot, guild: discord.Guild, listing_id: int):
    listing = db.get_market_listing(listing_id)
    if not listing or not listing["message_id"]:
        return
    try:
        await edit_components(bot, listing["channel_id"], listing["message_id"], listing_payload(guild, listing))
    except discord.HTTPException:
        logging.warning("Не удалось обновить объявление %s", listing_id)


async def close_listing(bot, guild: discord.Guild, listing: dict, reason: str):
    db.update_market_listing(listing["id"], status="closed", closed_reason=reason)
    await refresh_listing(bot, guild, listing["id"])


def can_edit(member: discord.Member, listing: dict) -> bool:
    return member.id == listing["user_id"] or can_manage(member)


def message_link(listing: dict) -> str:
    return f"https://discord.com/channels/{listing['guild_id']}/{listing['channel_id']}/{listing['message_id']}"


def guide_payload(guild: discord.Guild) -> dict:
    settings = db.get_market_settings(guild.id) or {"max_active": 3, "days": 7}
    rules = settings.get("rules_text") or DEFAULT_RULES.format(max_active=settings["max_active"], days=settings["days"])
    return build_market_guide_payload(max_active=settings["max_active"], days=settings["days"], rules_text=rules, guarantors=True)


async def refresh_guide(bot, guild: discord.Guild, repost: bool = False, channel=None) -> Optional[str]:
    """Обновляет (или отправляет заново) сообщение-инструкцию рынка. Возвращает текст ошибки или None."""
    settings = db.get_market_settings(guild.id) or {}
    target = channel or (guild.get_channel(settings["guide_channel_id"]) if settings.get("guide_channel_id") else None)
    if target is None:
        return "Канал инструкции не выбран: /setting market guide."
    if settings.get("guide_message_id") and not repost:
        try:
            await edit_components(bot, target.id, settings["guide_message_id"], guide_payload(guild))
            return None
        except discord.HTTPException:
            logging.warning("Не удалось обновить инструкцию рынка, отправляю заново")
    try:
        message = await post_components(bot, target, guide_payload(guild))
    except discord.HTTPException as e:
        return f"Не удалось отправить инструкцию: {e}"
    db.set_market_settings(guild.id, guide_channel_id=target.id, guide_message_id=int(message["id"]))
    return None


def my_listings_text(guild_id: int, user_id: int) -> str:
    rows = db.list_open_market_listings(guild_id, user_id)
    lines = [
        f"• №{r['id']} **{r['title']}** · откликов: {len(db.list_market_responses(r['id']))} · до <t:{r['expires_at']}:R> · "
        + (message_link(r) if r["message_id"] else "не опубликовано")
        for r in rows
    ]
    return "\n".join(lines) or "Открытых объявлений нет. Опубликуйте: /market post."


async def start_listing(interaction: discord.Interaction, kind: str):
    """Общая проверка и открытие формы для команды /market post и кнопок инструкции."""
    settings = db.get_market_settings(interaction.guild_id)
    if not settings or not settings["channel_id"]:
        await interaction.response.send_message("Рынок ещё не настроен: /setting market setup.", ephemeral=True)
        return
    if len(db.list_open_market_listings(interaction.guild_id, interaction.user.id)) >= settings["max_active"]:
        await interaction.response.send_message(
            f"У вас уже {settings['max_active']} открытых объявлений. Закройте одно из них (/market my).", ephemeral=True
        )
        return
    await interaction.response.send_modal(ListingModal(kind))


class RulesModal(discord.ui.Modal, title="Правила рынка"):
    def __init__(self, current: str):
        super().__init__(timeout=900)
        self.text = discord.ui.TextInput(
            label="Правила (по одному на строку)", style=discord.TextStyle.paragraph, max_length=900,
            required=False, default=current or None, placeholder="Пусто = стандартные правила",
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        db.set_market_settings(interaction.guild_id, rules_text=self.text.value.strip() or None)
        error = await refresh_guide(interaction.client, interaction.guild)
        await interaction.followup.send(error or "Правила рынка сохранены, инструкция обновлена.", ephemeral=True)


class ListingModal(discord.ui.Modal, title="Объявление на рынке"):
    def __init__(self, kind: str):
        super().__init__(timeout=900)
        self.kind = kind
        self.name = discord.ui.TextInput(label="Услуга (коротко)", max_length=80)
        self.price = discord.ui.TextInput(label="Цена", max_length=60, required=False, placeholder="Например: 500 или договорная")
        self.description = discord.ui.TextInput(
            label="Описание", style=discord.TextStyle.paragraph, max_length=900,
            placeholder="Что именно вы предлагаете или ищете",
        )
        self.notes = discord.ui.TextInput(
            label="Примечания", style=discord.TextStyle.paragraph, max_length=400, required=False,
            placeholder="Условия, сроки, ограничения",
        )
        self.contact = discord.ui.TextInput(label="Как связаться", max_length=100, required=False, placeholder="Ник в игре, время онлайн")
        for item in (self.name, self.price, self.description, self.notes, self.contact):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        settings = db.get_market_settings(guild.id)
        channel = guild.get_channel(settings["channel_id"]) if settings and settings["channel_id"] else None
        if channel is None:
            await interaction.followup.send("Рынок ещё не настроен: /setting market setup.", ephemeral=True)
            return
        if len(db.list_open_market_listings(guild.id, user.id)) >= settings["max_active"]:
            await interaction.followup.send(
                f"У вас уже {settings['max_active']} открытых объявлений. Закройте одно из них.", ephemeral=True
            )
            return
        now = int(time.time())
        listing_id = db.create_market_listing(
            guild.id, user.id, self.kind, self.name.value.strip(), self.price.value.strip() or None,
            self.description.value.strip(), self.notes.value.strip() or None, self.contact.value.strip() or None,
            user.display_avatar.url, now, now + settings["days"] * 86400,
        )
        listing = db.get_market_listing(listing_id)
        try:
            message = await post_components(interaction.client, channel, listing_payload(guild, listing))
        except discord.HTTPException as e:
            db.delete_market_listing(listing_id)
            logging.exception("Не удалось опубликовать объявление")
            await interaction.followup.send(f"Не удалось опубликовать (проверьте права бота в канале рынка): {e}", ephemeral=True)
            return
        db.update_market_listing(listing_id, channel_id=channel.id, message_id=int(message["id"]))
        link = f"https://discord.com/channels/{guild.id}/{channel.id}/{message['id']}"
        await interaction.followup.send(
            f"Объявление опубликовано: {link}\nОно будет актуально {settings['days']} дн.", ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы объявления", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class ResponseModal(discord.ui.Modal, title="Отклик на объявление"):
    def __init__(self, listing_id: int):
        super().__init__(timeout=900)
        self.listing_id = listing_id
        self.message = discord.ui.TextInput(
            label="Сообщение автору", style=discord.TextStyle.paragraph, max_length=400, required=False,
            placeholder="Когда вам удобно, что можете предложить",
        )
        self.add_item(self.message)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, user = interaction.guild, interaction.user
        listing = db.get_market_listing(self.listing_id)
        if not listing or listing["status"] != "open":
            await interaction.followup.send("Объявление уже закрыто.", ephemeral=True)
            return
        if user.id == listing["user_id"]:
            await interaction.followup.send("Это ваше объявление.", ephemeral=True)
            return
        text = self.message.value.strip()
        if not db.add_market_response(listing["id"], user.id, text, int(time.time())):
            await interaction.followup.send("Вы уже откликались на это объявление.", ephemeral=True)
            return
        await refresh_listing(interaction.client, guild, listing["id"])
        author = guild.get_member(listing["user_id"])
        note = f"{user.mention} откликнулся на ваше объявление «{listing['title']}»" + (f":\n{text}" if text else ".")
        notified = False
        if author:
            try:
                await author.send(note + f"\n{message_link(listing)}")
                notified = True
            except discord.HTTPException:
                pass
        if not notified:
            channel = guild.get_channel(listing["channel_id"])
            if channel:
                try:
                    await channel.send(
                        f"<@{listing['user_id']}>, новый отклик на «{listing['title']}»: {user.mention}",
                        delete_after=180, allowed_mentions=discord.AllowedMentions(users=[discord.Object(listing["user_id"])]),
                    )
                except discord.HTTPException:
                    pass
        await interaction.followup.send("Отклик отправлен автору.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы отклика", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class MarketView(discord.ui.View):
    """Обработчики кнопок под объявлением (внешний вид собирает content.build_market_payload)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Откликнуться", custom_id="mk:resp")
    async def respond(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing_by_message(interaction.message.id)
        if not listing or listing["status"] != "open":
            await interaction.response.send_message("Объявление уже закрыто.", ephemeral=True)
            return
        if interaction.user.id == listing["user_id"]:
            await interaction.response.send_message("Это ваше объявление.", ephemeral=True)
            return
        await interaction.response.send_modal(ResponseModal(listing["id"]))

    @discord.ui.button(label="Отклики", custom_id="mk:list")
    async def responses(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing_by_message(interaction.message.id)
        if not listing:
            await interaction.response.send_message("Объявление не найдено.", ephemeral=True)
            return
        rows = db.list_market_responses(listing["id"])
        if not can_edit(interaction.user, listing):
            await interaction.response.send_message(f"Откликов: {len(rows)}. Список виден автору.", ephemeral=True)
            return
        if not rows:
            await interaction.response.send_message("Откликов пока нет.", ephemeral=True)
            return
        lines = [f"• <@{r['user_id']}> · <t:{r['created_at']}:R>" + (f"\n  {r['message'][:150]}" if r["message"] else "") for r in rows[:15]]
        text = f"**Отклики на «{listing['title']}»**\n" + "\n".join(lines)
        await interaction.response.send_message(text[:1900], ephemeral=True, allowed_mentions=NO_PING)

    @discord.ui.button(label="Поднять", custom_id="mk:bump")
    async def bump(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing_by_message(interaction.message.id)
        if not listing or listing["status"] != "open":
            await interaction.response.send_message("Объявление уже закрыто.", ephemeral=True)
            return
        if not can_edit(interaction.user, listing):
            await interaction.response.send_message("Поднять может автор или персонал.", ephemeral=True)
            return
        now = int(time.time())
        wait = BUMP_COOLDOWN - (now - (listing["bumped_at"] or listing["created_at"] or 0))
        if wait > 0 and not can_manage(interaction.user):
            await interaction.response.send_message(
                f"Поднять можно раз в 12 часов. Подождите ещё около {wait // 3600 + 1} ч.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = guild.get_channel(listing["channel_id"])
        if channel is None:
            await interaction.followup.send("Канал рынка не найден.", ephemeral=True)
            return
        old_id = listing["message_id"]
        try:
            message = await post_components(interaction.client, channel, listing_payload(guild, listing))
        except discord.HTTPException as e:
            await interaction.followup.send(f"Не удалось поднять: {e}", ephemeral=True)
            return
        db.update_market_listing(listing["id"], message_id=int(message["id"]), bumped_at=now)
        try:
            await (await channel.fetch_message(old_id)).delete()
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            f"Объявление поднято: https://discord.com/channels/{guild.id}/{channel.id}/{message['id']}", ephemeral=True
        )

    @discord.ui.button(label="Предлагаю услугу", custom_id="mk:new_offer")
    async def new_offer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_listing(interaction, "offer")

    @discord.ui.button(label="Ищу услугу", custom_id="mk:new_request")
    async def new_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_listing(interaction, "request")

    @discord.ui.button(label="Гаранты", custom_id="mk:guarantors")
    async def guarantors(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await respond_components(interaction.client, interaction, guarantors_payload(interaction.guild))
        except discord.HTTPException:
            logging.exception("Не удалось показать список гарантов")
            if not interaction.response.is_done():
                await interaction.response.send_message("Не удалось показать гарантов. Сообщите персоналу.", ephemeral=True)

    @discord.ui.button(label="Мои объявления", custom_id="mk:mine")
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            my_listings_text(interaction.guild_id, interaction.user.id), ephemeral=True, allowed_mentions=NO_PING
        )

    @discord.ui.button(label="Закрыть", custom_id="mk:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        listing = db.get_market_listing_by_message(interaction.message.id)
        if not listing or listing["status"] != "open":
            await interaction.response.send_message("Объявление уже закрыто.", ephemeral=True)
            return
        if not can_edit(interaction.user, listing):
            await interaction.response.send_message("Закрыть может автор или персонал.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        reason = "закрыто автором" if interaction.user.id == listing["user_id"] else "закрыто персоналом"
        await close_listing(interaction.client, interaction.guild, listing, reason)
        await interaction.followup.send("Объявление закрыто.", ephemeral=True)


class Market(commands.Cog):
    market_grp = app_commands.Group(name="market", description="Рынок: услуги и объявления", guild_only=True)
    setting = app_commands.Group(name="market_setting", description="Настройки рынка", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.info_last = {}  # канал -> время последнего показа инструкции (защита от спама)

    async def cog_load(self):
        self.bot.add_view(MarketView())
        self.expire.start()

    async def cog_unload(self):
        self.expire.cancel()

    @tasks.loop(minutes=10)
    async def expire(self):
        """Закрывает объявления, у которых истёк срок."""
        for listing in db.list_expired_market_listings(int(time.time())):
            guild = self.bot.get_guild(listing["guild_id"])
            if guild is None:
                continue
            try:
                await close_listing(self.bot, guild, listing, "срок истёк")
            except Exception:
                logging.exception("Ошибка автозакрытия объявления")

    @expire.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()

    # ----- Для всех -----
    @market_grp.command(name="post", description="Опубликовать услугу или запрос на рынке")
    @app_commands.describe(kind="Что вы публикуете")
    @app_commands.choices(kind=KIND_CHOICES)
    async def market_post(self, interaction: discord.Interaction, kind: app_commands.Choice[str]):
        await start_listing(interaction, kind.value)

    @market_grp.command(name="my", description="Мои открытые объявления")
    async def market_my(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            my_listings_text(interaction.guild_id, interaction.user.id), ephemeral=True, allowed_mentions=NO_PING
        )

    @market_grp.command(name="guide", description="Как пользоваться рынком (видно только вам)")
    async def market_howto(self, interaction: discord.Interaction):
        payload = dict(guide_payload(interaction.guild))
        payload["flags"] = payload["flags"] | EPHEMERAL
        try:
            await respond_components(interaction.client, interaction, payload)
        except discord.HTTPException:
            logging.exception("Не удалось показать инструкцию рынка")
            if not interaction.response.is_done():
                await interaction.response.send_message("Не удалось показать инструкцию. Сообщите персоналу.", ephemeral=True)

    @market_grp.command(name="info", description="Показать всем в канале инструкцию рынка с кнопками")
    async def market_info(self, interaction: discord.Interaction):
        now = time.time()
        if now - self.info_last.get(interaction.channel_id, 0) < 60 and not can_manage(interaction.user):
            await interaction.response.send_message(
                "Инструкция уже показывалась в этом канале минуту назад. Прокрутите чат вверх.", ephemeral=True
            )
            return
        self.info_last[interaction.channel_id] = now
        try:
            await respond_components(interaction.client, interaction, guide_payload(interaction.guild))
        except discord.HTTPException:
            logging.exception("Не удалось показать инструкцию рынка всем")
            if not interaction.response.is_done():
                await interaction.response.send_message("Не удалось показать инструкцию. Сообщите персоналу.", ephemeral=True)

    @market_grp.command(name="search", description="Найти объявление по слову")
    @app_commands.describe(query="Что ищете: услуга или слово из описания")
    async def market_search(self, interaction: discord.Interaction, query: str):
        rows = db.search_market_listings(interaction.guild_id, query)
        lines = [
            f"• №{r['id']} **{r['title']}** · {r['price'] or 'договорная'} · <@{r['user_id']}> · "
            + (message_link(r) if r["message_id"] else "")
            for r in rows
        ]
        await interaction.response.send_message(
            "\n".join(lines)[:1900] or f"По запросу «{query}» ничего не найдено среди открытых объявлений.",
            ephemeral=True, allowed_mentions=NO_PING,
        )

    # ----- Для персонала -----
    @setting.command(name="setup", description="Настроить рынок: канал, гарант, лимит и срок объявлений")
    @app_commands.describe(
        channel="Канал рынка, куда публикуются объявления",
        guarantor_channel="Канал гаранта (покажется в списке гарантов), необязательно",
        guarantor_role="Роль гарантов (иначе бот ищет роль «Гарант»), необязательно",
        max_active="Сколько открытых объявлений может быть у одного человека",
        days="Сколько дней объявление актуально",
    )
    async def market_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        guarantor_channel: Optional[discord.abc.GuildChannel] = None,
        guarantor_role: Optional[discord.Role] = None,
        max_active: app_commands.Range[int, 1, 10] = 3,
        days: app_commands.Range[int, 1, 30] = 7,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.view_channel):
            await interaction.response.send_message(f"У бота нет права писать в {channel.mention}.", ephemeral=True)
            return
        fields = {"channel_id": channel.id, "max_active": max_active, "days": days}
        if guarantor_channel:
            fields["guarantor_channel_id"] = guarantor_channel.id
        if guarantor_role:
            fields["guarantor_role_id"] = guarantor_role.id
        db.set_market_settings(interaction.guild_id, **fields)
        await interaction.response.defer(ephemeral=True)
        if (db.get_market_settings(interaction.guild_id) or {}).get("guide_message_id"):
            await refresh_guide(interaction.client, interaction.guild)
        await interaction.followup.send(
            f"Рынок настроен: {channel.mention}. До {max_active} объявлений на человека, актуальность {days} дн."
            + (f"\nКанал гаранта: {guarantor_channel.mention}" if guarantor_channel else "")
            + (f"\nРоль гарантов: {guarantor_role.mention}" if guarantor_role else ""),
            ephemeral=True, allowed_mentions=NO_PING,
        )

    @setting.command(name="guide", description="Отправить в канал сообщение-инструкцию рынка")
    @app_commands.describe(channel="Канал для инструкции (по умолчанию канал рынка)")
    async def market_guide(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        settings = db.get_market_settings(interaction.guild_id)
        target = channel or (interaction.guild.get_channel(settings["channel_id"]) if settings and settings["channel_id"] else None)
        if target is None:
            await interaction.response.send_message("Сначала настройте рынок: /setting market setup.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        old = settings or {}
        if old.get("guide_channel_id") and old.get("guide_message_id"):
            old_channel = interaction.guild.get_channel(old["guide_channel_id"])
            if old_channel:
                try:
                    await (await old_channel.fetch_message(old["guide_message_id"])).delete()
                except discord.HTTPException:
                    pass
        db.set_market_settings(interaction.guild_id, guide_message_id=None)
        error = await refresh_guide(interaction.client, interaction.guild, repost=True, channel=target)
        await interaction.followup.send(error or f"Инструкция отправлена в {target.mention}.", ephemeral=True)

    @setting.command(name="rules", description="Изменить правила рынка в инструкции")
    async def market_rules(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        settings = db.get_market_settings(interaction.guild_id) or {"max_active": 3, "days": 7}
        current = settings.get("rules_text") or DEFAULT_RULES.format(max_active=settings["max_active"], days=settings["days"])
        await interaction.response.send_modal(RulesModal(current))

    @setting.command(name="close", description="Закрыть объявление по номеру (он указан в карточке)")
    @app_commands.describe(number="Номер объявления")
    async def market_close(self, interaction: discord.Interaction, number: int):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        listing = db.get_market_listing(number)
        if not listing or listing["guild_id"] != interaction.guild_id or listing["status"] != "open":
            await interaction.response.send_message("Открытого объявления с таким номером нет.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await close_listing(interaction.client, interaction.guild, listing, "закрыто персоналом")
        await interaction.followup.send("Объявление закрыто.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Market(bot))
