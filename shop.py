# shop.py — магазинный режим Eagle для сервера «Команда Westeria»
# Нужен discord.py 2.6+ (Components V2). Работает вместе с modes.py: команды доступны только в режиме «Магазин».
# Баннеры лежат в assets/westeria (memo.jpg, shop.jpg, news.jpg).
#
# Команды:
#   /памятка              — памятка + заказ одним сообщением (кнопка «Оставить заявку»)
#   /новость              — новость в канал новостей (форма для текста, можно картинку и пинг)
#   /отзыв                — оставить отзыв (звёзды + текст)
#   /правила_магазина     — изменить правила в памятке
#   /инструкция_отзывов   — инструкция в канал отзывов (как у рынка, всегда последним сообщением)
#   /заказы               — открытые заявки (для персонала)
#   /настройка_магазина   — каналы и роли магазина
#
# Как работает заявка: «Оставить заявку» → форма → карточка в канал логов.
# На карточке: Взять на рассмотрение / Принять / Отклонить / Создать временный канал,
# после принятия — «Заказ выполнен» (выдаёт роль клиента и просит отзыв).
# Человеку в личку приходит каждое изменение.

import json
import os
from datetime import datetime
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

try:
    import emojis as _em
except ImportError:
    _em = None

# ===== НАСТРОЙКИ =====
BRAND = "Команда Westeria"
SITE = "https://thep1ney.netlify.app/"
HERE = os.path.dirname(os.path.abspath(__file__))
BANNERS = os.path.join(HERE, "assets", "westeria")
DATA_FILE = os.path.join(HERE, "shop_data.json")

COLOR_WAIT = 0xB5B5B5    # ждёт / на рассмотрении
COLOR_OK = 0x3BA55D      # принята / выполнена
COLOR_NO = 0xED4245      # отклонена


def e(name: str, fallback: str = "") -> str:
    """Эмодзи бота в тексте: <:имя:id>, иначе обычный смайлик."""
    return _em.EMOJI.get(name, fallback) if _em else fallback


def pe(name: str, fallback: str) -> Union[discord.PartialEmoji, str]:
    """Эмодзи бота для кнопки."""
    if _em and name in _em.IDS:
        return discord.PartialEmoji(name=name, id=_em.IDS[name], animated=name in _em.ANIMATED)
    return fallback


STAR, STAR_OFF = "★", "☆"

DEFAULT = {
    "config": {"news": None, "reviews": None, "category": None, "logs": None, "staff": None, "client": None,
               "memo_ch": None, "memo_msg": None, "guide_msg": None, "rules": None},
    "counter": 0,
    "apps": {},
    "reviews": [],
}


# ===== ДАННЫЕ =====
def load() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k, v in DEFAULT.items():
                d.setdefault(k, json.loads(json.dumps(v)))
            return d
        except (OSError, json.JSONDecodeError):
            pass
    return json.loads(json.dumps(DEFAULT))


DATA = load()


def save():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(DATA, f, ensure_ascii=False, indent=2)


def cfg(key):
    return DATA["config"].get(key)


def stars(n: int) -> str:
    return STAR * n + STAR_OFF * (5 - n)


def rating() -> tuple:
    r = DATA["reviews"]
    if not r:
        return 0.0, 0
    return sum(x["stars"] for x in r) / len(r), len(r)


def banner(name: str) -> Optional[discord.File]:
    path = os.path.join(BANNERS, name)
    return discord.File(path, filename=name) if os.path.exists(path) else None


def is_staff(member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    try:
        from utils import can_manage
        if can_manage(member):
            return True
    except ImportError:
        pass
    if member.guild_permissions.manage_guild:
        return True
    rid = cfg("staff")
    return bool(rid and any(r.id == rid for r in member.roles))


def ch_link(guild_id: int, channel_id: Optional[int]) -> Optional[str]:
    return f"https://discord.com/channels/{guild_id}/{channel_id}" if channel_id else None


def today() -> str:
    return datetime.now().strftime("%d.%m.%Y")


# ===== ВНЕШНИЙ ВИД =====
DEFAULT_RULES = (
    "Сначала обсуждаем, потом платишь. Никаких «скинь деньги, потом посмотрим».\n"
    "Цену и сроки фиксируем в канале заявки. Что не обговорили, то не обещали.\n"
    "Мелкие правки по заданию после сдачи бесплатно. Новые функции — это новый заказ.\n"
    "Код и бот после оплаты твои. Хостинг бота оплачивается отдельно.\n"
    "Общаемся вежливо. Хамство и спам в заявках — отказ без объяснений."
)


def done_count() -> int:
    return sum(1 for a in DATA["apps"].values() if a["status"] == "done")


def stats_line() -> str:
    avg, cnt = rating()
    parts = [f"Выполнено заказов: **{done_count()}**"]
    parts.append(f"отзывы **{avg:.1f}** {STAR} ({cnt})" if cnt else "отзывов пока нет")
    return " · ".join(parts)


def rules_text() -> str:
    raw = cfg("rules") or DEFAULT_RULES
    return "\n".join(f"{e('greybutterfly', '•')} {line.strip()}" for line in raw.splitlines() if line.strip())


def memo_view(guild: discord.Guild, files: dict, team: list) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)

    # о нас
    c1 = discord.ui.Container(accent_colour=COLOR_WAIT)
    if "memo.jpg" in files:
        c1.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://memo.jpg")))
    c1.add_item(discord.ui.TextDisplay(
        f"# {e('whitebutterfly', '🦋')} {BRAND}\n"
        "Делаем ботов для Discord и сайты. Пишем с нуля под твой сервер, без шаблонов и чужих сборок. "
        "Хочешь заявки с кнопками, тикеты, свои войсы или что-то совсем своё — сделаем.\n"
        f"-# {stats_line()}"
    ))
    c1.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    if team:
        lines = [f"{e('whiteheart', '🤍')} <@{uid}> — {role}" for uid, role in team]
        c1.add_item(discord.ui.TextDisplay(f"### {e('steve', '👥')} Наша команда\n" + "\n".join(lines)))
        c1.add_item(discord.ui.Separator())
    c1.add_item(discord.ui.TextDisplay(
        f"### {e('discord', '💬')} Наш сайт\n"
        f"Там примеры работ, кто мы и как с нами связаться.\n{SITE}"
    ))
    links = [discord.ui.Button(label="Сайт", emoji="🌐", url=SITE)]
    for key, label, emj in (("reviews", "Отзывы", "⭐"), ("news", "Новости", "📰")):
        url = ch_link(guild.id, cfg(key))
        if url:
            links.append(discord.ui.Button(label=label, emoji=emj, url=url))
    c1.add_item(discord.ui.ActionRow(*links))
    v.add_item(c1)

    # правила
    c3 = discord.ui.Container(accent_colour=COLOR_WAIT)
    c3.add_item(discord.ui.TextDisplay(f"## {e('rules', '📌')} Правила\n{rules_text()}"))
    v.add_item(c3)

    # заказ
    c2 = discord.ui.Container(accent_colour=COLOR_WAIT)
    if "shop.jpg" in files:
        c2.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://shop.jpg")))
    c2.add_item(discord.ui.TextDisplay(f"# {e('grass', '🛒')} Заказать\n-# {stats_line()}"))
    c2.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    c2.add_item(discord.ui.TextDisplay(
        "### Как это работает\n"
        f"{e('whitebutterfly', '1.')} Жмёшь «Оставить заявку» и пишешь, что нужно\n"
        f"{e('whitebutterfly', '2.')} Мы смотрим заявку и отвечаем тебе в личку\n"
        f"{e('whitebutterfly', '3.')} Обсуждаем детали в отдельном канале\n"
        f"{e('whitebutterfly', '4.')} Получаешь готового бота и, если не сложно, оставляешь отзыв"
    ))
    c2.add_item(discord.ui.Separator())
    c2.add_item(discord.ui.TextDisplay(
        f"### {e('boost', '💎')} Цена\n"
        "Договорная. Всё зависит от того, сколько нужно сделать: простой бот с приветствием и большая система "
        "с базой данных стоят по-разному. Опиши задачу, и мы назовём цену."
    ))
    c2.add_item(discord.ui.ActionRow(
        discord.ui.Button(label="Оставить заявку", style=discord.ButtonStyle.success,
                          emoji=pe("whitebutterfly", "🦋"), custom_id="shop:apply"),
        discord.ui.Button(label="Мои заявки", style=discord.ButtonStyle.secondary, custom_id="shop:my"),
        discord.ui.Button(label="Как оставить отзыв", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="shop:guide"),
    ))
    v.add_item(c2)
    return v


def guide_view(guild: Optional[discord.Guild] = None, buttons: bool = True) -> discord.ui.LayoutView:
    """Инструкция по отзывам (как у рынка): висит последним сообщением в канале отзывов."""
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=COLOR_OK)
    c.add_item(discord.ui.TextDisplay(
        f"## ⭐ Как оставить отзыв\n-# {stats_line()}"
    ))
    c.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    c.add_item(discord.ui.TextDisplay(
        f"{e('whitebutterfly', '1.')} Отзыв оставляют после выполненного заказа. Бот сам пришлёт тебе в личку кнопки со звёздами.\n"
        f"{e('whitebutterfly', '2.')} Пропустил сообщение? Нажми «Оставить отзыв» ниже или напиши **/отзыв** (нужна роль клиента).\n"
        f"{e('whitebutterfly', '3.')} Выбери оценку от 1 до 5 и напиши пару слов: что понравилось, что нет.\n"
        f"{e('whitebutterfly', '4.')} Отзыв сразу появится в этом канале."
    ))
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(
        f"### {e('rules', '📌')} Про отзывы\n"
        f"{e('greybutterfly', '•')} Один заказ — один отзыв.\n"
        f"{e('greybutterfly', '•')} Пиши как есть, плохие оценки мы не удаляем.\n"
        f"{e('greybutterfly', '•')} Мат, оскорбления и реклама — удаляем.\n"
        f"{e('greybutterfly', '•')} Писать в канал руками нельзя, только через бота."
    ))
    if buttons:
        row = [discord.ui.Button(label="Оставить отзыв", style=discord.ButtonStyle.success, emoji="⭐", custom_id="shop:rvgen")]
        memo = ch_link(guild.id, cfg("memo_ch")) if guild else None
        if memo and cfg("memo_msg"):
            row.append(discord.ui.Button(label="Заказать", emoji=pe("whitebutterfly", "🦋"), url=f"{memo}/{cfg('memo_msg')}"))
        c.add_item(discord.ui.ActionRow(*row))
    v.add_item(c)
    return v


STATUS_LINE = {
    "pending": lambda a: f"{e('greybutterfly', '⚪')} ждёт рассмотрения",
    "claimed": lambda a: f"{e('greybutterfly', '🟡')} рассматривает <@{a['claimed_by']}>",
    "accepted": lambda a: f"{e('whitebutterfly', '🟢')} принята (<@{a['decided_by']}>)",
    "rejected": lambda a: f"{e('blackbutterfly', '🔴')} отклонена (<@{a['decided_by']}>)" + (f"\n> {a['reason']}" if a.get("reason") else ""),
    "done": lambda a: f"{e('whiteheart', '✅')} заказ выполнен (<@{a['decided_by']}>)",
}
STATUS_WORD = {"pending": "ждёт", "claimed": "на рассмотрении", "accepted": "принята", "rejected": "отклонена", "done": "выполнена"}


def app_view(a: dict, ping: bool = False) -> discord.ui.LayoutView:
    color = {"accepted": COLOR_OK, "done": COLOR_OK, "rejected": COLOR_NO}.get(a["status"], COLOR_WAIT)
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=color)
    head = f"## {e('whitebutterfly', '🦋')} Заявка #{a['num']}\n<@{a['user']}> · {a['name']}"
    if a.get("avatar"):
        c.add_item(discord.ui.Section(discord.ui.TextDisplay(head), accessory=discord.ui.Thumbnail(a["avatar"])))
    else:
        c.add_item(discord.ui.TextDisplay(head))
    c.add_item(discord.ui.Separator())
    parts = [f"**Что нужно**\n{a['what']}", f"**Подробнее**\n{a['details']}"]
    for key, label in (("server", "Сервер / проект"), ("budget", "Бюджет"), ("deadline", "Сроки")):
        if a.get(key):
            parts.append(f"**{label}**\n{a[key]}")
    c.add_item(discord.ui.TextDisplay("\n\n".join(parts)))
    c.add_item(discord.ui.Separator())
    status = f"**Статус:** {STATUS_LINE[a['status']](a)}"
    if a.get("temp"):
        status += f"\n**Канал:** <#{a['temp']}>"
    c.add_item(discord.ui.TextDisplay(status))
    n = a["num"]
    if a["status"] in ("pending", "claimed"):
        c.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Взять на рассмотрение", style=discord.ButtonStyle.secondary, custom_id=f"shop:claim:{n}", disabled=a["status"] == "claimed"),
            discord.ui.Button(label="Принять", style=discord.ButtonStyle.success, custom_id=f"shop:accept:{n}"),
            discord.ui.Button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id=f"shop:reject:{n}"),
            discord.ui.Button(label="Создать временный канал", style=discord.ButtonStyle.primary, custom_id=f"shop:temp:{n}", disabled=bool(a.get("temp"))),
        ))
    elif a["status"] == "accepted":
        c.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Заказ выполнен", style=discord.ButtonStyle.success, emoji=pe("whiteheart", "✅"), custom_id=f"shop:done:{n}"),
            discord.ui.Button(label="Создать временный канал", style=discord.ButtonStyle.primary, custom_id=f"shop:temp:{n}", disabled=bool(a.get("temp"))),
        ))
    foot = f"-# {a['date']}"
    if ping and cfg("staff"):
        foot += f" · <@&{cfg('staff')}>"
    c.add_item(discord.ui.TextDisplay(foot))
    v.add_item(c)
    return v


def temp_view(a: dict) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=COLOR_WAIT)
    c.add_item(discord.ui.TextDisplay(
        f"## {e('whitebutterfly', '🦋')} Заявка #{a['num']}\n"
        f"<@{a['user']}>, привет! Здесь обсуждаем твой заказ: что именно нужно, сроки и цену. "
        "Можно кидать скрины и примеры."
    ))
    c.add_item(discord.ui.TextDisplay(f"**Что нужно:** {a['what']}"))
    c.add_item(discord.ui.ActionRow(
        discord.ui.Button(label="Закрыть канал", style=discord.ButtonStyle.secondary, custom_id=f"shop:close:{a['num']}"),
    ))
    v.add_item(c)
    return v


def review_ask_view(oid: str) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=COLOR_OK)
    c.add_item(discord.ui.TextDisplay(
        f"## {e('whiteheart', '🤍')} Спасибо, что выбрал нас!\n"
        "Если не сложно, поставь оценку. Нам это правда помогает."
    ))
    c.add_item(discord.ui.ActionRow(*[
        discord.ui.Button(label=f"{i} {STAR}", style=discord.ButtonStyle.secondary, custom_id=f"shop:rv:{oid}:{i}")
        for i in range(1, 6)
    ]))
    v.add_item(c)
    return v


def review_post_view(user: discord.abc.User, n: int, text: str, oid: str) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=COLOR_OK if n >= 4 else COLOR_WAIT if n == 3 else COLOR_NO)
    who = f"**{user.display_name}**" + (f" · заявка #{oid}" if oid != "0" else "")
    c.add_item(discord.ui.Section(
        discord.ui.TextDisplay(f"## {stars(n)}\n{who}\n<@{user.id}>"),
        accessory=discord.ui.Thumbnail(user.display_avatar.url),
    ))
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(f">>> {text}"))
    c.add_item(discord.ui.TextDisplay(f"-# {e('whitebutterfly', '')} {today()} · {BRAND}"))
    v.add_item(c)
    return v


def news_view(title: str, text: str, author: discord.abc.User, img: Optional[str], ping: Optional[str], has_banner: bool) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=COLOR_WAIT)
    if has_banner:
        c.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://news.jpg")))
    c.add_item(discord.ui.TextDisplay(f"# {title}"))
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(text))
    if img:
        c.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{img}")))
    c.add_item(discord.ui.Separator())
    c.add_item(discord.ui.TextDisplay(f"-# {e('whitebutterfly', '')} {today()} · {author.display_name}" + (f" · {ping}" if ping else "")))
    v.add_item(c)
    return v


def simple_view(text: str, color: int = COLOR_WAIT, *rows) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView(timeout=None)
    c = discord.ui.Container(accent_colour=color)
    c.add_item(discord.ui.TextDisplay(text))
    for r in rows:
        c.add_item(r)
    v.add_item(c)
    return v


# ===== ФОРМЫ =====
class ApplyModal(discord.ui.Modal):
    def __init__(self, cog: "Shop"):
        super().__init__(title="Заявка на бота")
        self.cog = cog
        self.what = discord.ui.TextInput(label="Что нужно сделать?", placeholder="Например: бот для клана с заявками", max_length=120)
        self.details = discord.ui.TextInput(label="Подробнее: команды и функции", style=discord.TextStyle.paragraph,
                                            placeholder="Что бот должен уметь. Чем подробнее, тем точнее цена", max_length=1500)
        self.server = discord.ui.TextInput(label="Для какого сервера или проекта", required=False, max_length=100)
        self.budget = discord.ui.TextInput(label="Бюджет, если есть", required=False, placeholder="Можно оставить пустым", max_length=60)
        self.deadline = discord.ui.TextInput(label="Сроки", required=False, placeholder="Например: за неделю", max_length=60)
        for i in (self.what, self.details, self.server, self.budget, self.deadline):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.create_app(interaction, {
            "what": self.what.value, "details": self.details.value, "server": self.server.value,
            "budget": self.budget.value, "deadline": self.deadline.value,
        })


class RejectModal(discord.ui.Modal):
    def __init__(self, cog: "Shop", num: str):
        super().__init__(title=f"Отклонить заявку #{num}")
        self.cog, self.num = cog, num
        self.reason = discord.ui.TextInput(label="Причина (необязательно)", style=discord.TextStyle.paragraph, required=False, max_length=400)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.decide(interaction, self.num, "rejected", self.reason.value)


class ReviewModal(discord.ui.Modal):
    def __init__(self, cog: "Shop", oid: str, n: int):
        super().__init__(title=f"Отзыв · {stars(n)}")
        self.cog, self.oid, self.n = cog, oid, n
        self.text = discord.ui.TextInput(label="Пара слов о работе", style=discord.TextStyle.paragraph, min_length=5, max_length=800)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.post_review(interaction, self.oid, self.n, self.text.value)


class NewsModal(discord.ui.Modal):
    def __init__(self, cog: "Shop", image: Optional[discord.Attachment], ping: Optional[str]):
        super().__init__(title="Новость")
        self.cog, self.image, self.ping = cog, image, ping
        self.head = discord.ui.TextInput(label="Заголовок", max_length=100)
        self.body = discord.ui.TextInput(label="Текст", style=discord.TextStyle.paragraph, max_length=3000,
                                         placeholder="Можно **жирный**, списки, ссылки")
        self.add_item(self.head)
        self.add_item(self.body)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.post_news(interaction, self.head.value, self.body.value, self.image, self.ping)


class RulesModal(discord.ui.Modal):
    def __init__(self, cog: "Shop", current: str):
        super().__init__(title="Правила в памятке")
        self.cog = cog
        self.text = discord.ui.TextInput(label="Каждое правило с новой строки", style=discord.TextStyle.paragraph,
                                         default=current[:1500], max_length=1500)
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        DATA["config"]["rules"] = self.text.value
        save()
        await interaction.response.send_message("Правила сохранены, памятка обновлена.", ephemeral=True)
        await self.cog.refresh_memo(interaction.guild)


# ===== КОГ =====
class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- кнопки (работают и после перезапуска) ---
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("shop:"):
            return
        if interaction.guild is None and not cid.startswith("shop:rv"):
            return  # в личке работают только кнопки отзыва
        p = cid.split(":")
        act = p[1]
        try:
            if act == "apply":
                if not cfg("logs"):
                    await interaction.response.send_message("Заявки пока не принимаются: не настроен канал логов.", ephemeral=True)
                    return
                open_app = next((a for a in DATA["apps"].values() if a["user"] == interaction.user.id and a["status"] in ("pending", "claimed")), None)
                if open_app:
                    await interaction.response.send_message(f"У тебя уже есть заявка **#{open_app['num']}**, она ещё на рассмотрении.", ephemeral=True)
                    return
                await interaction.response.send_modal(ApplyModal(self))
            elif act == "guide":
                await interaction.response.send_message(view=guide_view(interaction.guild, buttons=False), ephemeral=True)
            elif act == "rvgen":
                if not self.can_review(interaction.user, "0"):
                    await interaction.response.send_message("Отзыв могут оставить те, кто уже заказывал у нас.", ephemeral=True)
                    return
                await interaction.response.send_message(view=review_ask_view("0"), ephemeral=True)
            elif act == "my":
                await self.my_apps(interaction)
            elif act in ("claim", "accept", "reject", "temp", "done"):
                if not is_staff(interaction.user):
                    await interaction.response.send_message("Это может только персонал.", ephemeral=True)
                    return
                num = p[2]
                if act == "claim":
                    await self.claim(interaction, num)
                elif act == "accept":
                    await self.decide(interaction, num, "accepted")
                elif act == "reject":
                    await interaction.response.send_modal(RejectModal(self, num))
                elif act == "temp":
                    await self.make_temp(interaction, num)
                elif act == "done":
                    await self.decide(interaction, num, "done")
            elif act == "close":
                if not is_staff(interaction.user):
                    await interaction.response.send_message("Закрыть канал может только персонал.", ephemeral=True)
                    return
                await interaction.response.send_message(
                    view=simple_view("Точно закрыть канал? Его удалит.", COLOR_NO, discord.ui.ActionRow(
                        discord.ui.Button(label="Да, закрыть", style=discord.ButtonStyle.danger, custom_id=f"shop:closeok:{p[2]}"))),
                    ephemeral=True)
            elif act == "closeok":
                await self.close_temp(interaction, p[2])
            elif act == "rv":
                oid, n = p[2], int(p[3])
                if not self.can_review(interaction.user, oid):
                    await interaction.response.send_message("Отзыв по этой заявке уже оставлен или заказ ещё не выполнен.", ephemeral=True)
                    return
                await interaction.response.send_modal(ReviewModal(self, oid, n))
        except discord.HTTPException:
            pass

    # --- заявки ---
    async def notify(self, user_id: int, text: str):
        user = self.bot.get_user(user_id)
        try:
            user = user or await self.bot.fetch_user(user_id)
            await user.send(text)
        except discord.HTTPException:
            pass

    async def refresh(self, a: dict):
        ch = self.bot.get_channel(a["log_ch"])
        if not ch:
            return
        try:
            msg = await ch.fetch_message(a["log_msg"])
            await msg.edit(view=app_view(a))
        except discord.HTTPException:
            pass

    async def create_app(self, interaction: discord.Interaction, f: dict):
        guild, user = interaction.guild, interaction.user
        logs = guild.get_channel(cfg("logs")) if cfg("logs") else None
        if logs is None:
            await interaction.response.send_message("Не настроен канал логов. Напиши владельцу.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        DATA["counter"] += 1
        num = f"{DATA['counter']:02d}"
        a = {"num": num, "user": user.id, "name": user.display_name, "avatar": user.display_avatar.url,
             "status": "pending", "claimed_by": None, "decided_by": None, "temp": None, "reviewed": False,
             "date": today(), "guild": guild.id, **f}
        msg = await logs.send(view=app_view(a, ping=True), allowed_mentions=discord.AllowedMentions(roles=True))
        a["log_ch"], a["log_msg"] = logs.id, msg.id
        DATA["apps"][num] = a
        save()
        await interaction.followup.send(
            f"{e('whitebutterfly', '🦋')} Заявка **#{num}** отправлена! Мы посмотрим её и напишем тебе в личку. "
            "Проверь, что у тебя открыты сообщения от участников сервера.", ephemeral=True)

    async def claim(self, interaction: discord.Interaction, num: str):
        a = DATA["apps"].get(num)
        if not a:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        a["status"], a["claimed_by"] = "claimed", interaction.user.id
        save()
        await interaction.response.edit_message(view=app_view(a))
        await self.notify(a["user"], f"{e('greybutterfly', '👀')} Твою заявку **#{num}** на **{interaction.guild.name}** взяли на рассмотрение. Скоро напишем.")

    async def decide(self, interaction: discord.Interaction, num: str, result: str, reason: str = ""):
        a = DATA["apps"].get(num)
        if not a:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        a["status"], a["decided_by"] = result, interaction.user.id
        if reason:
            a["reason"] = reason
        save()
        if interaction.type == discord.InteractionType.component:
            await interaction.response.edit_message(view=app_view(a))
        else:  # из формы отказа
            await interaction.response.send_message(f"Заявка **#{num}** отклонена.", ephemeral=True)
            await self.refresh(a)
        g = interaction.guild.name
        if result == "accepted":
            await self.notify(a["user"], f"{e('whitebutterfly', '🦋')} Заявку **#{num}** на **{g}** приняли! Скоро создадим канал, где обсудим детали и цену.")
        elif result == "rejected":
            await self.notify(a["user"], f"{e('blackbutterfly', '🖤')} Заявку **#{num}** на **{g}** отклонили." + (f"\nПричина: {reason}" if reason else ""))
        elif result == "done":
            await self.finish(interaction.guild, a)

    async def finish(self, guild: discord.Guild, a: dict):
        await self.refresh_memo(guild)
        await self.refresh_guide(guild)
        member = guild.get_member(a["user"])
        role = guild.get_role(cfg("client")) if cfg("client") else None
        if member and role:
            try:
                await member.add_roles(role, reason=f"Заявка #{a['num']} выполнена")
            except discord.HTTPException:
                pass
        user = member or self.bot.get_user(a["user"])
        try:
            user = user or await self.bot.fetch_user(a["user"])
            await user.send(view=review_ask_view(a["num"]))
        except discord.HTTPException:
            pass
        if a.get("temp"):
            ch = guild.get_channel(a["temp"])
            if ch:
                try:
                    await ch.send(view=review_ask_view(a["num"]))
                except discord.HTTPException:
                    pass

    async def make_temp(self, interaction: discord.Interaction, num: str):
        a = DATA["apps"].get(num)
        if not a:
            await interaction.response.send_message("Заявка не найдена.", ephemeral=True)
            return
        guild = interaction.guild
        if a.get("temp") and guild.get_channel(a["temp"]):
            await interaction.response.send_message(f"Канал уже есть: <#{a['temp']}>", ephemeral=True)
            return
        await interaction.response.defer()
        cat = guild.get_channel(cfg("category")) if cfg("category") else None
        if not isinstance(cat, discord.CategoryChannel):
            cat = discord.utils.get(guild.categories, name="Заявки") or await guild.create_category("Заявки")
            DATA["config"]["category"] = cat.id
        member = guild.get_member(a["user"])
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        if member:
            ow[member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        staff = guild.get_role(cfg("staff")) if cfg("staff") else None
        if staff:
            ow[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        ch = await guild.create_text_channel(f"заявка-{num}", category=cat, overwrites=ow, topic=f"Заявка #{num}")
        await ch.send(view=temp_view(a), allowed_mentions=discord.AllowedMentions(users=True))
        a["temp"] = ch.id
        save()
        await interaction.edit_original_response(view=app_view(a))
        await self.notify(a["user"], f"{e('whitebutterfly', '🦋')} По заявке **#{num}** создали канал: {ch.mention}. Заходи, обсудим.")

    async def close_temp(self, interaction: discord.Interaction, num: str):
        a = DATA["apps"].get(num)
        ch = interaction.guild.get_channel(a["temp"]) if a and a.get("temp") else interaction.channel
        if a:
            a["temp"] = None
            save()
            await self.refresh(a)
        await interaction.response.send_message("Закрываю…", ephemeral=True)
        try:
            await ch.delete(reason=f"Заявка #{num}: канал закрыт")
        except discord.HTTPException:
            pass

    async def my_apps(self, interaction: discord.Interaction):
        mine = [a for a in DATA["apps"].values() if a["user"] == interaction.user.id]
        if not mine:
            await interaction.response.send_message("У тебя пока нет заявок. Жми «Оставить заявку».", ephemeral=True)
            return
        lines = [f"**#{a['num']}** · {a['what'][:60]} · {STATUS_WORD[a['status']]}" + (f" · <#{a['temp']}>" if a.get("temp") else "") for a in mine[-10:]]
        await interaction.response.send_message(view=simple_view("## Мои заявки\n" + "\n".join(lines)), ephemeral=True)

    # --- отзывы ---
    def can_review(self, user: discord.abc.User, oid: str) -> bool:
        if oid == "0":
            role = cfg("client")
            return not role or (isinstance(user, discord.Member) and any(r.id == role for r in user.roles))
        a = DATA["apps"].get(oid)
        return bool(a and a["user"] == user.id and a["status"] == "done" and not a.get("reviewed"))

    async def post_review(self, interaction: discord.Interaction, oid: str, n: int, text: str):
        guild = interaction.guild
        if guild is None:
            a = DATA["apps"].get(oid)
            guild = self.bot.get_guild(a["guild"]) if a else None
        target = guild.get_channel(cfg("reviews")) if guild and cfg("reviews") else None
        if target is None:
            await interaction.response.send_message("Канал отзывов не настроен. Напиши владельцу.", ephemeral=True)
            return
        view = review_post_view(interaction.user, n, text, oid)
        if isinstance(target, discord.ForumChannel):
            await target.create_thread(name=f"{stars(n)} · {interaction.user.display_name}"[:100], view=view)
        else:
            await target.send(view=view, allowed_mentions=discord.AllowedMentions.none())
        DATA["reviews"].append({"user": interaction.user.id, "stars": n, "app": oid, "date": today()})
        if oid in DATA["apps"]:
            DATA["apps"][oid]["reviewed"] = True
        save()
        await interaction.response.send_message(f"Спасибо! Отзыв опубликован {stars(n)}", ephemeral=True)
        await self.refresh_guide(guild, repost=not isinstance(target, discord.ForumChannel))
        await self.refresh_memo(guild)

    # --- живые сообщения: памятка и инструкция отзывов ---
    async def team(self, guild: discord.Guild) -> list:
        out, seen = [], set()
        try:
            info = await self.bot.application_info()
            owner = info.team.owner_id if info.team else info.owner.id
        except discord.HTTPException:
            owner = None
        if owner and guild.get_member(owner):
            out.append((owner, "основатель, разработка"))
            seen.add(owner)
        role = guild.get_role(cfg("staff")) if cfg("staff") else None
        if role:
            for m in role.members:
                if not m.bot and m.id not in seen:
                    out.append((m.id, role.name))
                    seen.add(m.id)
        return out[:10]

    def memo_files(self) -> dict:
        files = {n: banner(n) for n in ("memo.jpg", "shop.jpg")}
        return {k: v for k, v in files.items() if v}

    async def refresh_memo(self, guild: discord.Guild):
        ch = guild.get_channel(cfg("memo_ch")) if cfg("memo_ch") else None
        if not ch or not cfg("memo_msg"):
            return
        try:
            msg = await ch.fetch_message(cfg("memo_msg"))
            files = self.memo_files()
            await msg.edit(view=memo_view(guild, files, await self.team(guild)), attachments=list(files.values()),
                           allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def refresh_guide(self, guild: Optional[discord.Guild], repost: bool = False, channel=None) -> Optional[str]:
        if guild is None:
            return "Нет сервера."
        ch = channel or (guild.get_channel(cfg("reviews")) if cfg("reviews") else None)
        if ch is None:
            return "Канал отзывов не выбран: /настройка_магазина отзывы."
        if isinstance(ch, discord.ForumChannel):
            return "Канал отзывов — форум. Инструкцию закрепи постом вручную или выбери обычный канал."
        old = cfg("guide_msg")
        if old and not repost:
            try:
                msg = await ch.fetch_message(old)
                await msg.edit(view=guide_view(guild))
                return None
            except discord.HTTPException:
                pass
        if old and repost:
            try:
                await (await ch.fetch_message(old)).delete()
            except discord.HTTPException:
                pass
        try:
            msg = await ch.send(view=guide_view(guild))
        except discord.HTTPException as ex:
            return f"Не удалось отправить инструкцию: {ex}"
        DATA["config"]["guide_msg"] = msg.id
        save()
        return None

    # --- новости ---
    async def post_news(self, interaction, title, text, image, ping):
        ch = interaction.guild.get_channel(cfg("news")) if cfg("news") else interaction.channel
        files = []
        b = banner("news.jpg")
        if b:
            files.append(b)
        img_name = None
        if image:
            ext = os.path.splitext(image.filename)[1].lower() or ".png"
            img_name = "news_image" + ext
            files.append(await image.to_file(filename=img_name))
        mention = {"everyone": "@everyone", "here": "@here"}.get(ping or "")
        view = news_view(title, text, interaction.user, img_name, mention, has_banner=bool(b))
        await ch.send(view=view, files=files, allowed_mentions=discord.AllowedMentions(everyone=bool(mention)))
        await interaction.response.send_message(f"{e('whitebutterfly', '✅')} Новость опубликована в {ch.mention}", ephemeral=True)

    # ===== СЛЭШ-КОМАНДЫ =====
    @app_commands.command(name="памятка", description="Опубликовать памятку с заказом в этом канале")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def memo(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        files = self.memo_files()
        msg = await interaction.channel.send(view=memo_view(interaction.guild, files, await self.team(interaction.guild)),
                                             files=list(files.values()), allowed_mentions=discord.AllowedMentions.none())
        DATA["config"]["memo_ch"], DATA["config"]["memo_msg"] = interaction.channel.id, msg.id
        save()
        await self.refresh_guide(interaction.guild)
        await interaction.followup.send("Памятка опубликована. Цифры и команда в ней будут обновляться сами.", ephemeral=True)

    @app_commands.command(name="правила_магазина", description="Изменить правила в памятке")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def shop_rules(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        await interaction.response.send_modal(RulesModal(self, cfg("rules") or DEFAULT_RULES))

    @app_commands.command(name="инструкция_отзывов", description="Отправить инструкцию по отзывам в канал отзывов")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def reviews_guide(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        err = await self.refresh_guide(interaction.guild, repost=True)
        await interaction.followup.send(err or "Инструкция отправлена. После каждого отзыва она будет опускаться вниз.", ephemeral=True)

    @app_commands.command(name="новость", description="Опубликовать новость")
    @app_commands.describe(картинка="Картинка к новости (необязательно)", пинг="Кого упомянуть")
    @app_commands.choices(пинг=[app_commands.Choice(name="Никого", value="none"), app_commands.Choice(name="@here", value="here"),
                                app_commands.Choice(name="@everyone", value="everyone")])
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def news(self, interaction: discord.Interaction, картинка: Optional[discord.Attachment] = None,
                   пинг: Optional[app_commands.Choice[str]] = None):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        await interaction.response.send_modal(NewsModal(self, картинка, пинг.value if пинг else None))

    @app_commands.command(name="отзыв", description="Оставить отзыв о работе")
    @app_commands.guild_only()
    async def review(self, interaction: discord.Interaction):
        if not self.can_review(interaction.user, "0"):
            await interaction.response.send_message("Отзыв могут оставить те, кто уже заказывал.", ephemeral=True)
            return
        await interaction.response.send_message(view=review_ask_view("0"), ephemeral=True)

    @app_commands.command(name="заказы", description="Открытые заявки")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def orders(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        active = [a for a in DATA["apps"].values() if a["status"] in ("pending", "claimed", "accepted")]
        text = "\n".join(f"**#{a['num']}** · <@{a['user']}> · {a['what'][:50]} · {STATUS_WORD[a['status']]}" for a in active) or "Открытых заявок нет."
        avg, cnt = rating()
        await interaction.response.send_message(
            view=simple_view(f"## Заявки\n{text}\n\n-# Всего: {len(DATA['apps'])} · отзывы {avg:.1f} {STAR} ({cnt})"), ephemeral=True)

    @app_commands.command(name="настройка_магазина", description="Каналы и роли магазина")
    @app_commands.describe(логи="Куда приходят заявки", новости="Канал новостей", отзывы="Канал или форум отзывов",
                           категория="Категория для временных каналов", персонал="Роль персонала", клиент="Роль после выполненного заказа")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def shop_setting(self, interaction: discord.Interaction,
                           логи: Optional[discord.TextChannel] = None,
                           новости: Optional[discord.TextChannel] = None,
                           отзывы: Optional[Union[discord.TextChannel, discord.ForumChannel]] = None,
                           категория: Optional[discord.CategoryChannel] = None,
                           персонал: Optional[discord.Role] = None,
                           клиент: Optional[discord.Role] = None):
        if not interaction.user.guild_permissions.administrator and not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message("Нет доступа.", ephemeral=True)
            return
        for key, val in (("logs", логи), ("news", новости), ("reviews", отзывы), ("category", категория), ("staff", персонал), ("client", клиент)):
            if val is not None:
                DATA["config"][key] = val.id
        save()
        ch = lambda k: f"<#{cfg(k)}>" if cfg(k) else "не задано"
        rl = lambda k: f"<@&{cfg(k)}>" if cfg(k) else "не задано"
        await interaction.response.send_message(
            f"## Настройки магазина\nЗаявки (логи): {ch('logs')}\nНовости: {ch('news')}\nОтзывы: {ch('reviews')}\n"
            f"Категория каналов: {ch('category')}\nПерсонал: {rl('staff')}\nКлиент: {rl('client')}\n"
            "-# Памятка обновилась сама. Инструкцию в отзывы: /инструкция_отзывов.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await self.refresh_memo(interaction.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
