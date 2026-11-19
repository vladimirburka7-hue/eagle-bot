"""Эмодзи бота (Developer Portal -> ваше приложение -> Emojis).

Бот сам узнаёт при запуске, какие эмодзи анимированные, и подставляет
правильный код (<a:имя:ID> или <:имя:ID>). Вручную ничего отмечать не нужно.

Чтобы добавить новое эмодзи: загрузите его в портал и допишите строку в IDS
(короткое имя: ID). Также любое эмодзи доступно по его настоящему названию
из портала, например EMOJI["13768whitebutterfly"].
"""
import logging
import re

IDS = {
    "discord": 1565474916952838355,
    "whitebutterfly": 1565474816713166970,
    "grass": 1565474729366921236,
    "boost": 1565474701239652432,
    "whiteheart": 1565472357844389968,
    "rules": 1565472194648215622,
    "blackbutterfly": 1565472152604385484,
    "greybutterfly": 1565472064222007387,
    "steve": 1566033998109610024,
}

# Смысловые имена -> названия эмодзи в портале (цифры в начале названия можно не писать:
# "tick" найдёт «21607tick»). Можно указать несколько — берётся первое найденное.
# Красные значки (Administrator, Moderator…) оставлены для ролей, здесь только белые.
ALIASES = {
    "ok": ["green"],                     # принято, включено
    "no": ["red"],                       # отклонено, выключено
    "wait": ["blue"],                    # ждёт, информация
    "warn": ["warn"],                    # нарушение, предупреждение
    "tick": ["tick"],                    # галочка в пунктах
    "title": ["whitebutterfly"],         # заголовки сообщений
    "admin": ["medalla"],                # капитан, владелец
    "mod": ["greyboosterroleicon"],      # персонал, модерация
    "gmod": ["discordfistbump"],         # стаки
    "dev": ["discordlaptop"],
    "squad": ["sword"],                  # отряды
    "voice": ["discordmicrophone"],      # войсы
    "host": ["play"],                    # игры, развлечения, активность
    "ghost": ["s_ghost"],                # участники
    "eye": ["goat"],                     # доступ, видимость
    "partner": ["discordheart"],
    "heart": ["744060whiteheart", "whiteheart"],
    "market": ["whitegemheart"],
    "news": ["sparklywhiteflower"],
    "flower": ["prettyflower"],
    "mc": ["5833minecraft", "minecraft"],
    "inactive": ["s_ghost"],
}

# Если эмодзи нет в портале (или Discord недоступен), подставляется обычный смайлик.
FALLBACK = {
    "ok": "🟢", "no": "🔴", "wait": "🔵", "warn": "⚠️", "tick": "✔️", "title": "🦋", "admin": "🏅", "mod": "🛡️",
    "gmod": "🤜", "dev": "💻", "squad": "⚔️", "voice": "🎙️", "host": "🎮", "ghost": "👻", "eye": "👁️",
    "partner": "🤝", "heart": "🤍", "market": "💎", "news": "✨", "flower": "🌼", "mc": "🟫", "inactive": "👻",
    "rules": "📜", "boost": "💎", "steve": "🙂", "grass": "🟩", "discord": "💬", "whiteheart": "🤍",
    "whitebutterfly": "🦋", "blackbutterfly": "🦋", "greybutterfly": "🦋",
}


class _Emojis(dict):
    """Словарь эмодзи, который никогда не падает: нет эмодзи — обычный смайлик."""

    def __missing__(self, key):
        return FALLBACK.get(key, "▫️")


# Заполняется при запуске (load_app_emojis). До этого считаем все обычными.
ANIMATED = set()
EMOJI = _Emojis({name: f"<:{name}:{eid}>" for name, eid in IDS.items()})


def button_emoji(name: str) -> dict:
    """Эмодзи для кнопки в формате Discord API (нет такого — обычный смайлик)."""
    if name not in IDS:
        return {"name": FALLBACK.get(name, "▫️")}
    return {"id": str(IDS[name]), "name": name, "animated": name in ANIMATED}


def butterfly(fallback: str = "🦋", name: str = "whitebutterfly") -> dict:
    """Бабочка бота для кнопок (анимированная, если так загружена в портале), иначе обычный смайлик."""
    return button_emoji(name) if name in IDS else {"name": fallback}


def icon(name: str, fallback: str) -> dict:
    """Эмодзи для кнопки: своё из портала, если оно загружено (имя name), иначе обычный смайлик fallback."""
    if name in IDS:
        return button_emoji(name)
    return {"name": fallback}


_CUSTOM = re.compile(r"^<(a?):([A-Za-z0-9_]{2,32}):(\d{15,25})>$")
_UNICODE_EMOJI = re.compile(
    "^[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\u2900-\u297F\u2B00-\u2BFF"
    "\u3030\u303D\u3297\u3299\u00A9\u00AE\u203C\u2049\u2122\u2139\u24C2\u25AA-\u25FE\uFE0F\u200D]{1,8}$"
)


def emoji_to_button(text):
    """Строка -> эмодзи в формате кнопки Discord или None, если не распознано.

    Принимает: короткое имя эмодзи бота (boost), код <:имя:ID> / <a:имя:ID> или обычный смайлик.
    """
    text = (text or "").strip()
    if not text:
        return None
    if text in IDS:
        return button_emoji(text)
    match = _CUSTOM.match(text)
    if match:
        return {"id": match.group(3), "name": match.group(2), "animated": bool(match.group(1))}
    if _UNICODE_EMOJI.match(text):
        return {"name": text}
    return None


async def load_app_emojis(bot):
    """Читает эмодзи приложения из Discord и обновляет EMOJI / ANIMATED."""
    try:
        emojis = await bot.fetch_application_emojis()
    except Exception as e:  # нет интернета, старая версия discord.py и т.п.
        logging.warning("Не удалось загрузить эмодзи бота: %s", e)
        return
    by_id = {e.id: e for e in emojis}
    for alias, eid in IDS.items():
        found = by_id.get(eid)
        if found:
            EMOJI[alias] = str(found)
            if found.animated:
                ANIMATED.add(alias)
            else:
                ANIMATED.discard(alias)
        else:
            logging.warning("Эмодзи %s (ID %s) не найдено среди эмодзи бота", alias, eid)
    for found in emojis:
        EMOJI.setdefault(found.name, str(found))
        if found.name not in IDS:  # новые эмодзи из портала (например vc_rename) сразу доступны для кнопок
            IDS[found.name] = found.id
            if found.animated:
                ANIMATED.add(found.name)
    by_short = {}
    for found in emojis:  # «21607tick» -> «tick»
        by_short.setdefault(re.sub(r"^\d+", "", found.name).lower(), found)
    by_name = {e.name: e for e in emojis}
    for alias, names in ALIASES.items():  # смысловые имена (ok, no, admin…) -> загруженные эмодзи
        for real in names:
            found = by_name.get(real) or by_short.get(re.sub(r"^\d+", "", real).lower())
            if found:
                IDS[alias] = found.id
                EMOJI[alias] = str(found)
                (ANIMATED.add if found.animated else ANIMATED.discard)(alias)
                break
        else:
            logging.warning("Эмодзи для «%s» не найдено (%s), будет обычный смайлик", alias, ", ".join(names))
    logging.info("Эмодзи бота загружены: %d, анимированных: %d",
                 len(emojis), sum(1 for e in emojis if e.animated))
