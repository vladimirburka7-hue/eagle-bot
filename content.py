"""Сообщения бота в стиле Components V2.

Стиль: один эмодзи на сообщение (в заголовке), простые слова на «ты»,
пара строк текста — остальное делают кнопки. Цветные кружки только в логах персонала.
"""
import io
import json

from emojis import EMOJI, IDS, butterfly, button_emoji, icon

OPEN_IDS = {
    "clan": "tk:open:clan",
    "staff": "tk:open:staff",
    "report": "tk:open:report",
    "help": "tk:open:help",
}

IS_COMPONENTS_V2 = 32768
EPHEMERAL = 64


def _t(content):
    return {"type": 10, "content": content}


def _line():
    return {"type": 14, "spacing": 1, "divider": True}


def _gap():
    return {"type": 14, "spacing": 1, "divider": False}


def _box(*items, color=None):
    box = {"type": 17, "components": list(items)}
    if color is not None:
        box["accent_color"] = color
    return box


def _quote(text):
    """Чужой текст (ответы в анкете, сообщение нарушителя) — цитатой."""
    return "\n".join(f"> {line}" for line in str(text).splitlines() or ["—"])


def _code(text):
    """Оставлено для совместимости со старыми модулями: просто текст."""
    return str(text)


def _note(text):
    """Мелкий серый текст."""
    return "\n".join(f"-# {line}" for line in str(text).splitlines() if line.strip())


def _title(emoji=None, title=""):
    """Заголовок сообщения со своим эмодзи по смыслу (по умолчанию — бабочка)."""
    return _t(f"## {EMOJI[emoji or 'title']} {title}")


def _sub(_emoji, title, body):
    return _t(f"**{title}**\n{body}")


def _head(text, avatar_url=None):
    """Шапка с аватаркой справа (если есть)."""
    if avatar_url:
        return {"type": 9, "components": [_t(text)], "accessory": {"type": 11, "media": {"url": avatar_url}}}
    return _t(text)


def _clean(text):
    return (str(text) if text is not None else "").replace("```", "ʼʼʼ")


def _btn(label, custom_id, style=2, disabled=False, emoji=None):
    btn = {"type": 2, "style": style, "label": label, "custom_id": custom_id, "disabled": disabled}
    if emoji and emoji in IDS:  # только свои эмодзи бота; нет такого — кнопка без значка
        btn["emoji"] = button_emoji(emoji)
    return btn


def _row(*buttons):
    return {"type": 1, "components": list(buttons)}


def people(n):
    """1 человек, 2 человека, 5 человек."""
    n = int(n)
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} человека"
    return f"{n} человек"


def _join_ru(words):
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " и " + words[-1]


# Цвета рамок
BLUE, GREEN, RED, GREY, DARK = 0x5865F2, 0x3BA55D, 0xED4245, 0x4F545C, 0x2B2D31


def build_rules_payload():
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(
            _title("rules", "Правила"),
            _t(f"{EMOJI['heart']} Уважай других: без оскорблений и травли\n"
               f"{EMOJI['discord']} Без спама, рекламы и NSFW\n"
               f"{EMOJI['voice']} В войсе не шуми и не записывай без спроса\n"
               f"{EMOJI['eye']} Никому не давай пароль и токен"),
            _line(),
            _t(f"{EMOJI['warn']} Предупреждение → мут → кик → бан\n"
               "-# Не согласен с наказанием? Подай жалобу через заявки."),
        )],
    }


# (вид, подпись кнопки, стиль: 1 синяя, 2 серая, 3 зелёная, 4 красная)
PANEL_ITEMS = [
    ("clan", "В клан", 3, "mc"),
    ("staff", "В персонал", 1, "mod"),
    ("report", "Жалоба", 2, "warn"),
    ("help", "Вопрос", 2, "discord"),
]


def build_panel_payload():
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(
            _title("heart", "Заявки"),
            _t("Хочешь в клан или в команду сервера? Жми кнопку и заполни анкету, ответим в личку."),
            _t(f"-# {EMOJI['wait']} ждёт   {EMOJI['ok']} принята   {EMOJI['no']} отклонена"),
            _row(*[_btn(label, OPEN_IDS[kind], style, emoji=em) for kind, label, style, em in PANEL_ITEMS]),
        )],
    }


DECISION_KINDS = ("clan", "staff")


def build_app_payload(*, kind, title, user_id, answers, status, color,
                      avatar_url=None, temp_channel_id=None, decided=False,
                      ping_role_id=None, ping=False):
    """Заявка в канале логов (для персонала): кто, статус, ответы, кнопки."""
    header = f"## {EMOJI['heart']} {title}\n<@{user_id}>\n{status}"
    if temp_channel_id:
        header += f"\nКанал: <#{temp_channel_id}>"
    items = [_head(header, avatar_url), _line()]
    for label, value in answers:
        items.append(_t(f"**{label}**\n{_quote(_clean(value).strip() or '—')}"))

    buttons = [_btn("Взять", "ap:claim", 2, decided, "wait"),
               _btn("Принять" if kind in DECISION_KINDS else "Решено", "ap:accept", 2, decided, "ok")]
    if kind in DECISION_KINDS:
        buttons.append(_btn("Отклонить", "ap:reject", 2, decided, "no"))
    buttons.append(_btn("Канал", "ap:temp", 2, False, "discord"))
    items.extend([_line(), _row(*buttons)])

    components = []
    if ping_role_id:
        components.append(_t(f"<@&{ping_role_id}>"))
    components.append({"type": 17, "accent_color": color, "components": items})
    mentions = {"parse": []}
    if ping and ping_role_id:
        mentions["roles"] = [str(ping_role_id)]
    return {"flags": IS_COMPONENTS_V2, "components": components, "allowed_mentions": mentions}


def build_profile_payload(*, name, user_id, avatar_url, nick, first_join, squad,
                          violations, rewards, notes=None, color=DARK, stack=None):
    """Профиль участника. rewards: список (эмодзи, название); notes=None скрывает блок."""
    rows = [f"{EMOJI['mc']} Ник: **{nick or 'не указан'}**", f"{EMOJI['squad']} Отряд: **{squad}**"]
    if stack:
        rows.append(f"{EMOJI['gmod']} Стак: **{stack}**")
    rows.append(f"{EMOJI['heart']} На сервере с {first_join}")
    if violations:
        rows.append(f"{EMOJI['warn']} Нарушений: {violations}")
    header = f"## {EMOJI['steve']} {name}\n<@{user_id}>\n\n" + "\n".join(rows)
    items = [_head(header, avatar_url)]
    if rewards:
        items.append(_t(f"{EMOJI['admin']} **Награды**\n" + "  ".join(f"{emoji} {title}" for emoji, title in rewards[:20])))
    if notes:
        items.append(_t(f"**Заметки**\n{_clean(notes)}"))
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": color, "components": items}],
        "allowed_mentions": {"parse": []},
    }


def build_report_payload(*, squad, week_text, captain_id, avatar_url, stats, answers, color=BLUE):
    """Отчёт капитана."""
    header = f"## {EMOJI['rules']} Отчёт: {squad}\n{week_text}, капитан <@{captain_id}>"
    items = [_head(header, avatar_url), _line(), _t(f"{EMOJI['host']} **Активность**\n{stats}")]
    for label, value in answers:
        items.append(_t(f"**{label}**\n{_quote(_clean(value) or '—')}"))
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": color, "components": items}],
        "allowed_mentions": {"parse": []},
    }


def _mentions_off():
    return {"parse": []}


def build_stacks_main_payload():
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(
            _title("gmod", "Стаки"),
            _t("Собери свою команду: бот сам создаст роль и каналы.\n"
               "-# Капитан создаёт стак и принимает заявки. Один игрок — один стак."),
            _row(_btn("Создать", "st:create", 3, emoji="admin"), _btn("Вступить", "st:join", 1, emoji="tick"),
                 _btn("Мой стак", "st:me", emoji="ghost"), _btn("Выйти", "st:leave")),
        )],
        "allowed_mentions": _mentions_off(),
    }


def build_stack_panel_payload(*, name, captain_id, members, max_members, applicants, description=""):
    """Панель в канале стака: состав и заявки."""
    head = f"## {EMOJI['gmod']} {name}\n{EMOJI['admin']} Капитан <@{captain_id}>, {len(members)}/{max_members}"
    if description:
        head += "\n" + _clean(description)
    items = [_t(head), _line(), _t(f"{EMOJI['squad']} **Состав**\n" + (" ".join(f"<@{uid}>" for uid in members[:30]) or "пока пусто"))]
    if applicants:
        items.append(_t(f"{EMOJI['wait']} **Хотят вступить**\n" + " ".join(f"<@{uid}>" for uid, _l in applicants[:30])))

    def select(custom_id, placeholder):
        if applicants:
            options = [{"label": label[:100] or "участник", "value": str(uid)} for uid, label in applicants[:25]]
            disabled = False
        else:
            options = [{"label": "Заявок нет", "value": "0"}]
            disabled = True
        return _row({"type": 3, "custom_id": custom_id, "placeholder": placeholder,
                     "options": options, "min_values": 1, "max_values": 1, "disabled": disabled})

    items.append(select("st:accept", "Принять в стак"))
    items.append(select("st:decline", "Отклонить заявку"))
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": BLUE, "components": items}],
        "allowed_mentions": _mentions_off(),
    }


def build_stack_control_payload(*, name, guests_open, limit):
    """Панель капитана."""
    state = "гости могут заходить" if guests_open else "только для стака"
    limit_text = f"лимит {limit}" if limit else "без лимита"

    def user_select(custom_id, placeholder):
        return _row({"type": 5, "custom_id": custom_id, "placeholder": placeholder, "min_values": 1, "max_values": 1})

    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(
            _t(f"## {EMOJI['admin']} Управление стаком\n{EMOJI['voice']} Войс: {state}, {limit_text}."),
            _row(_btn("Лимит", "st:limit"), _btn("Закрыть для гостей" if guests_open else "Открыть для гостей", "st:guests"),
                 _btn("Название", "st:rename"), _btn("Распустить", "st:disband", 4, emoji="no")),
            user_select("st:permit", "Пустить гостя в войс"),
            user_select("st:remove", "Исключить из стака"),
            user_select("st:transfer", "Передать капитана"),
        )],
        "allowed_mentions": _mentions_off(),
    }


def _unique_stacks(stacks):
    seen, out = set(), []
    for st in stacks:
        key = (st["name"].lower(), st["captain_id"])
        if key not in seen:
            seen.add(key)
            out.append(st)
    return out


def build_stack_list_payload(stacks, max_members):
    """Каталог стаков для канала."""
    stacks = _unique_stacks(stacks)
    lines = [f"{EMOJI['gmod']} **{st['name']}** {st['count']}/{max_members}, капитан <@{st['captain_id']}>" for st in stacks[:20]]
    if len(stacks) > 20:
        lines.append(f"и ещё {len(stacks) - 20}")
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(_title("gmod", "Стаки сервера"), _t("\n".join(lines) or "Пока ни одного. Создай первый!"))],
        "allowed_mentions": _mentions_off(),
    }


def build_memo_payload(*, title, intro, total, squads, stacks, max_members, structure_text,
                       structure_code=False, banner_name=None, links=(), extra=(), activities=None,
                       show_squads=True, show_stacks=True, show_structure=True, show_members=True,
                       squads_title=None, stacks_title=None, structure_title=None, buttons_title=None,
                       activities_title=None):
    """Памятка-обзор одним сообщением: кто мы, отряды, стаки, структура, кнопки.

    squads: [{title, count, captains: [id]}]; stacks: [{name, captain_id, count}];
    links: [(подпись, url, имя_эмодзи|None)].
    """
    items = []
    if banner_name:
        items.extend([{"type": 12, "items": [{"media": {"url": f"attachment://{banner_name}"}}]}, _gap()])
    head = f"## {EMOJI['title']} {title}"
    if show_members:
        head += f"\n-# {EMOJI['heart']} {total} участников"
    if intro:
        head += "\n" + _note(_clean(intro))
    items.append(_t(head))
    if activities:
        items.append(_t(f"{EMOJI['mc']} **{activities_title or 'Чем занимаемся'}**\n{activities}"))

    if show_squads:
        squad_lines = []
        for sq in squads:
            caps = " ".join(f"<@{i}>" for i in sq["captains"][:2])
            squad_lines.append(f"{EMOJI['squad']} **{sq['title']}** · {sq['count']}" + (f" · {EMOJI['admin']} {caps}" if caps else ""))
        items.extend([_line(), _t(f"### {squads_title or 'Отряды'}\n" + ("\n".join(squad_lines) or "Пока нет."))])

    if show_stacks:
        seen, stack_lines = set(), []
        for st in stacks:
            key = (st["name"].lower(), st["captain_id"])
            if key in seen:
                continue
            seen.add(key)
            stack_lines.append(f"{EMOJI['gmod']} **{st['name']}** · {st['count']}/{max_members} · <@{st['captain_id']}>")
        more = len(stack_lines) - 10
        stack_lines = stack_lines[:10] + ([_note(f"…и ещё {more}")] if more > 0 else [])
        items.extend([_line(), _t(f"### {stacks_title or 'Стаки'}\n" + ("\n".join(stack_lines) or "Пока нет. Создайте первый."))])

    if show_structure:
        body = _quote(_clean(structure_text)) if structure_code else (structure_text or "—")
        items.extend([_line(), _t(f"### {structure_title or 'Структура'}\n{body}")])

    buttons = []
    for label, url, emoji in links:
        button = {"type": 2, "style": 5, "label": label, "url": url}
        if emoji:
            button["emoji"] = button_emoji(emoji)
        buttons.append(button)
    for item in extra:  # свои кнопки: {label, style, custom_id | url, emoji(dict)|None}
        button = {"type": 2, "style": item["style"], "label": item["label"][:80]}
        if item.get("url"):
            button["url"] = item["url"]
        else:
            button["custom_id"] = item["custom_id"]
        if item.get("emoji"):
            button["emoji"] = item["emoji"]
        buttons.append(button)
    buttons = buttons[:10]  # лимит компонентов в одном сообщении
    if buttons:
        items.append(_line())
        if buttons_title:
            items.append(_t(f"### {buttons_title}"))
        items.extend({"type": 1, "components": buttons[i:i + 5]} for i in range(0, len(buttons), 5))
    return {"flags": IS_COMPONENTS_V2, "components": [_box(*items)], "allowed_mentions": _mentions_off()}


def parse_sections(text):
    """Разбивает текст кнопки на разделы. Строка «## Заголовок» начинает раздел,
    текст до первого заголовка идёт без заголовка. Возвращает [(заголовок|None, текст)]."""
    sections, head, buf = [], None, []
    for line in (text or "").splitlines():
        if line.startswith("## "):
            if head is not None or any(x.strip() for x in buf):
                sections.append((head, "\n".join(buf).strip()))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if head is not None or any(x.strip() for x in buf):
        sections.append((head, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if h or b]


def build_button_payload(*, title, sections, extra_blocks=(), image_name=None, emoji=None):
    """Ответ «только для тебя» после кнопки или команды: заголовок и короткие блоки."""
    items = [_title(emoji, title)]
    for head, body in list(sections) + list(extra_blocks):
        body = _clean(body) or "—"
        items.append(_t(f"**{head}**\n{body}" if head else body))
    if image_name:
        items.append({"type": 12, "items": [{"media": {"url": f"attachment://{image_name}"}}]})
    return {
        "flags": IS_COMPONENTS_V2 | EPHEMERAL,
        "components": [_box(*items)],
        "allowed_mentions": _mentions_off(),
    }


async def respond_components(bot, interaction, payload, upload=None):
    """Отвечает на нажатие кнопки сообщением Components V2 (в том числе видимым только нажавшему)."""
    from discord import File
    from discord.http import Route

    route = Route(
        "POST", "/interactions/{interaction_id}/{interaction_token}/callback",
        interaction_id=interaction.id, interaction_token=interaction.token,
    )
    data = dict(payload)
    if upload is None:
        return await bot.http.request(route, json={"type": 4, "data": data})
    raw, filename = upload
    data["attachments"] = [{"id": 0, "filename": filename}]
    file = File(io.BytesIO(raw), filename=filename)
    form = [
        {"name": "payload_json", "value": json.dumps({"type": 4, "data": data}, ensure_ascii=False),
         "content_type": "application/json"},
        {"name": "files[0]", "value": file.fp, "filename": filename, "content_type": "application/octet-stream"},
    ]
    return await bot.http.request(route, form=form, files=[file])



def build_market_payload(*, kind, title, user_id, price, description, notes, contact, avatar_url,
                         expires_at, status, closed_reason, responses, guarantors=False, number=None):
    """Карточка объявления на рынке."""
    is_open = status == "open"
    label = "Предлагаю" if kind == "offer" else "Ищу"
    head = f"## {EMOJI['market']} {label}: {title}\n<@{user_id}>, цена: **{price or 'договорная'}**"
    head += f"\n{EMOJI['ok']} открыто" if is_open else f"\n{EMOJI['no']} закрыто: {closed_reason or 'автор закрыл'}"
    items = [_head(head, avatar_url), _t(_clean(description) or "—")]
    if notes:
        items.append(_t(f"{EMOJI['rules']} **Примечания**\n{_clean(notes)}"))
    if contact:
        items.append(_t(f"{EMOJI['discord']} **Связь**\n{_clean(contact)}"))
    meta = [f"откликов: {responses}"]
    if number:
        meta.append(f"№{number}")
    if is_open and expires_at:
        meta.append(f"до <t:{int(expires_at)}:R>")
    items.append(_t(_note(", ".join(meta))))
    buttons = [_btn("Откликнуться", "mk:resp", 3, not is_open, "tick"), _btn("Отклики", "mk:list"),
               _btn("Поднять", "mk:bump", 2, not is_open), _btn("Закрыть", "mk:close", 2, not is_open)]
    if guarantors:
        buttons.append(_btn("Гаранты", "mk:guarantors"))
    items.append(_row(*buttons))
    color = GREY if not is_open else (GREEN if kind == "offer" else BLUE)
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": color, "components": items}],
        "allowed_mentions": _mentions_off(),
    }


def build_market_guide_payload(*, max_active, days, rules_text, guarantors=True):
    """Сообщение для канала рынка."""
    items = [
        _title("market", "Рынок"),
        _t(f"Продаёшь или ищешь услугу? Жми кнопку и заполни форму. "
           f"Отклики придут тебе в личку, объявление висит {days} дн."),
    ]
    if rules_text:
        items.append(_t(f"**Правила**\n{_clean(rules_text)}"))
    buttons = [_btn("Предлагаю", "mk:new_offer", 3, emoji="market"), _btn("Ищу", "mk:new_request", 1, emoji="eye")]
    if guarantors:
        buttons.append(_btn("Гаранты", "mk:guarantors", emoji="mod"))
    buttons.append(_btn("Мои", "mk:mine", emoji="ghost"))
    items.append(_row(*buttons))
    return {"flags": IS_COMPONENTS_V2, "components": [_box(*items)], "allowed_mentions": _mentions_off()}


def build_voice_panel_payload(*, owner_id, name, state, limit, trusted, banned, image_name=None, ephemeral=False):
    """Панель управления временной голосовой комнатой."""
    access = {"open": "открыта для всех", "locked": "закрыта, заходят доверенные", "private": "скрыта от всех"}[state]
    dot = {"open": "ok", "locked": "wait", "private": "no"}[state]
    info = (f"## {EMOJI['voice']} {name.replace(chr(96), chr(39))}\n"
            f"{EMOJI['admin']} <@{owner_id}>\n{EMOJI[dot]} Комната {access}, " + (f"лимит {limit}." if limit else "без лимита."))

    def pick(cid, placeholder):
        return _row({"type": 5, "custom_id": cid, "placeholder": placeholder, "min_values": 1, "max_values": 1})

    top = [{"type": 12, "items": [{"media": {"url": f"attachment://{image_name}"}}]}] if image_name else []
    return {
        "flags": IS_COMPONENTS_V2 | (EPHEMERAL if ephemeral else 0),
        "components": [{"type": 17, "accent_color": BLUE, "components": top + [
            _t(info),
            _row(_btn("Название", "vc:name", emoji="vc_rename"), _btn("Лимит", "vc:limit", emoji="vc_list"),
                 _btn("Доступ", "vc:access", emoji="vc_lock"), _btn("Удалить", "vc:delete", 4, emoji="vc_delete")),
            pick("vc:trust", "Доверять / убрать доверие"),
            pick("vc:kick", "Выгнать"),
            pick("vc:ban", "Заблокировать / разблокировать"),
            pick("vc:owner", "Передать комнату"),
        ]}],
        "allowed_mentions": _mentions_off(),
    }


def build_hub_payload():
    """Сообщение для канала с войсами."""
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [_box(
            _title("voice", "Свои войсы"),
            _t("Зайди в канал создания, и появится твоя комната. Название и доступ меняются кнопками внутри."),
            _row(_btn("Как это работает", "vh:info", emoji="wait"), _btn("Настроить свою", "vh:funcs", 1, emoji="voice")),
        )],
        "allowed_mentions": _mentions_off(),
    }


CATEGORY_NAMES = {"mat": "мат", "insult": "оскорбление", "hate": "унижение по признаку", "threat": "угроза",
                  "custom": "слово из списка сервера", "mild": "мягкое слово"}


def build_modcenter_payload(*, active, today, enabled, mute_minutes, delete_messages, extra_words, reading_ok):
    """Центр управления для чата персонала."""
    auto = (f"Автомодерация включена: мут {mute_minutes} мин" + (", сообщение удаляется." if delete_messages else "."))\
        if enabled else "Автомодерация выключена."
    info = (f"## {EMOJI['mod']} Центр управления\n{EMOJI['warn']} В муте: **{active}**, за сутки: **{today}**\n"
            f"{EMOJI['ok' if enabled else 'no']} {auto}")
    if not reading_ok:
        info += "\nБот не видит текст сообщений: включи Message Content Intent и USE_MESSAGE_CONTENT=1."
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": RED, "components": [
            _t(info),
            _row({"type": 5, "custom_id": "mc:user", "placeholder": "Выбери участника", "min_values": 1, "max_values": 1}),
            _row(_btn("Мут", "mc:mute", 4, emoji="warn"), _btn("Продлить", "mc:extend"), _btn("Снять", "mc:lift", 3, emoji="ok"),
                 _btn("История", "mc:hist", emoji="rules")),
            _row(_btn("Кто в муте", "mc:active"), _btn("Выключить" if enabled else "Включить", "mc:toggle"),
                 _btn("Слова", "mc:words"), _btn("Обновить", "mc:refresh")),
        ]}],
        "allowed_mentions": _mentions_off(),
    }


def build_modlog_payload(*, user_id, channel_id, category, word, excerpt, minutes, until_ts, status, closed=False):
    """Карточка автоматического мута в логе."""
    kind = CATEGORY_NAMES.get(category, category)
    text = (f"## {EMOJI['warn']} Мут: {kind}\n<@{user_id}> в <#{channel_id}>, слово `{word}`\n"
            f"{minutes} мин, до <t:{until_ts}:t>. {status}")
    return {"flags": IS_COMPONENTS_V2,
            "components": [{"type": 17, "accent_color": GREY if closed else RED, "components": [
                _t(text), _t(_quote(_clean(excerpt or "—")[:600])),
                _row(_btn("+15 мин", "am:ext15", 2, closed), _btn("+1 час", "am:ext60", 2, closed),
                     _btn("Снять", "am:lift", 3, closed, "ok")),
            ]}],
            "allowed_mentions": _mentions_off()}


def build_news_payload(title, text, media=None, plain=False):
    """Новость: заголовок, текст и по желанию картинка или файл."""
    items = [_title("news", title), _t(_clean(text))]
    if media:
        kind, filename = media
        if kind == "image":
            items.append({"type": 12, "items": [{"media": {"url": f"attachment://{filename}"}}]})
        else:
            items.append({"type": 13, "file": {"url": f"attachment://{filename}"}})
    return {"flags": IS_COMPONENTS_V2, "components": [_box(*items)]}


# ---------- Баннер сервера (картинка сверху каждого сообщения бота) ----------
import copy
import os

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BANNER_DIR = os.path.join(ASSETS_DIR, "banners")
BANNER_TAG = "eagle_banner"  # по этому слову в имени файла бот узнаёт свой баннер
_BANNER_CACHE = {}


def banner_file(guild_id):
    """Путь к баннеру сервера или None, если баннер не включён."""
    if not guild_id:
        return None
    for ext in (".jpg", ".png", ".gif", ".webp"):
        path = os.path.join(BANNER_DIR, f"{guild_id}{ext}")
        if os.path.exists(path):
            return path
    return None


BANNER_ENABLED = False  # баннер сверху сообщений убран: новые сообщения идут без него


def banner_upload(guild_id):
    """(байты, имя_файла) баннера сервера или None."""
    if not BANNER_ENABLED:
        return None
    path = banner_file(guild_id)
    if not path:
        return None
    mtime = os.path.getmtime(path)
    cached = _BANNER_CACHE.get(path)
    if not cached or cached[0] != mtime:
        with open(path, "rb") as f:
            cached = (mtime, f.read())
        _BANNER_CACHE[path] = cached
    return cached[1], BANNER_TAG + os.path.splitext(path)[1]


def _is_banner_block(block):
    if not isinstance(block, dict) or block.get("type") != 12:
        return False
    return any(BANNER_TAG in ((it.get("media") or {}).get("url") or "") for it in block.get("items", []))


def strip_banner(components):
    out = []
    for c in components:
        if _is_banner_block(c):
            continue
        if isinstance(c, dict) and c.get("type") == 17:
            c = dict(c)
            c["components"] = [x for x in c.get("components", []) if not _is_banner_block(x)]
        out.append(c)
    return out


def add_banner(payload, filename):
    """Копия payload с баннером в самом верху (внутри первой рамки, если она есть)."""
    data = copy.deepcopy(payload)
    comps = strip_banner(data.get("components") or [])
    block = {"type": 12, "items": [{"media": {"url": f"attachment://{filename}"}}]}
    if comps and comps[0].get("type") == 17:
        comps[0]["components"] = [block] + comps[0].get("components", [])
    else:
        comps = [block] + comps
    data["components"] = comps
    return data


def _guild_of(bot, channel=None, channel_id=None):
    if channel is None and channel_id is not None:
        channel = bot.get_channel(int(channel_id))
    guild = getattr(channel, "guild", None)
    return guild.id if guild else None


async def _send(bot, method, path, payload, files=(), keep=(), **ids):
    """Запрос к API с файлами. files: [(байты, имя)], keep: ID вложений, которые остаются в сообщении."""
    from discord import File
    from discord.http import Route

    route = Route(method, path, **ids)
    files, keep = list(files), list(keep)
    if not files and not keep:
        return await bot.http.request(route, json=payload)
    data = dict(payload)
    data["attachments"] = [{"id": k} for k in keep] + [{"id": i, "filename": name} for i, (_, name) in enumerate(files)]
    if not files:
        return await bot.http.request(route, json=data)
    objs = [File(io.BytesIO(raw), filename=name) for raw, name in files]
    form = [{"name": "payload_json", "value": json.dumps(data, ensure_ascii=False), "content_type": "application/json"}]
    for i, (obj, (_, name)) in enumerate(zip(objs, files)):
        form.append({"name": f"files[{i}]", "value": obj.fp, "filename": name, "content_type": "application/octet-stream"})
    return await bot.http.request(route, form=form, files=objs)


async def post_components(bot, channel, payload, upload=None):
    """Отправляет сообщение Components V2 через API Discord (с баннером сервера, если он включён).

    upload: (байты, имя_файла) если нужно приложить файл.
    """
    files = []
    banner = banner_upload(_guild_of(bot, channel))
    if banner and not (payload.get("flags", 0) & EPHEMERAL):
        payload = add_banner(payload, banner[1])
        files.append(banner)
    if upload is not None:
        files.append(upload)
    return await _send(bot, "POST", "/channels/{channel_id}/messages", payload, files, channel_id=channel.id)


async def edit_components(bot, channel_id, message_id, payload, upload=None):
    """Редактирует сообщение Components V2 через API Discord (баннер сервера сохраняется)."""
    path = "/channels/{channel_id}/messages/{message_id}"
    ids = {"channel_id": channel_id, "message_id": message_id}
    banner = banner_upload(_guild_of(bot, channel_id=channel_id))
    if not banner:
        return await _send(bot, "PATCH", path, payload, [upload] if upload else [], **ids)
    try:
        current = await bot.http.get_message(channel_id, message_id)
        attachments = current.get("attachments", [])
    except Exception:
        attachments = []
    old = next((a for a in attachments if BANNER_TAG in a.get("filename", "")), None)
    reuse = old is not None and old["filename"] == banner[1]
    payload = add_banner(payload, banner[1])
    if upload is None:
        keep = [a["id"] for a in attachments if reuse or a is not old]
    else:
        keep = [old["id"]] if reuse else []
    files = ([] if reuse else [banner]) + ([upload] if upload else [])
    return await _send(bot, "PATCH", path, payload, files, keep, **ids)


def _clean_media(node, by_url, by_id):
    """Готовит компоненты из ответа API к повторной отправке: картинки сообщения -> attachment://имя."""
    if isinstance(node, list):
        return [_clean_media(x, by_url, by_id) for x in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("media", "file") and isinstance(value, dict):
            url = value.get("url") or ""
            name = by_id.get(str(value.get("attachment_id"))) or by_url.get(url.split("?")[0])
            out[key] = {"url": f"attachment://{name}" if name else url}
        else:
            out[key] = _clean_media(value, by_url, by_id)
    return out


async def rebanner_message(bot, channel_id, message_id, guild_id, add=True):
    """Добавляет (или убирает) баннер у уже отправленного сообщения бота. True, если сообщение изменено."""
    raw = await bot.http.get_message(channel_id, message_id)
    if not (raw.get("flags", 0) & IS_COMPONENTS_V2):
        return False
    attachments = raw.get("attachments", [])
    by_url = {a["url"].split("?")[0]: a["filename"] for a in attachments if a.get("url")}
    by_id = {str(a["id"]): a["filename"] for a in attachments}
    comps = _clean_media(raw.get("components", []), by_url, by_id)
    had = any(_is_banner_block(c) or (c.get("type") == 17 and any(_is_banner_block(x) for x in c.get("components", [])))
              for c in comps if isinstance(c, dict))
    payload = {"components": strip_banner(comps), "allowed_mentions": {"parse": []}}
    keep = [a["id"] for a in attachments if BANNER_TAG not in a.get("filename", "")]
    files = []
    if add:
        banner = banner_upload(guild_id)
        if not banner:
            return False
        payload = add_banner(payload, banner[1])
        files.append(banner)
    elif not had:
        return False
    else:
        payload["attachments"] = [{"id": k} for k in keep]  # убрать сам файл баннера из сообщения
    await _send(bot, "PATCH", "/channels/{channel_id}/messages/{message_id}", payload, files, keep,
                channel_id=channel_id, message_id=message_id)
    return True
