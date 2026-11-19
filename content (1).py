"""Готовые сообщения в стиле Components V2: правила, панель заявок, новости."""
import io
import json

from emojis import EMOJI, button_emoji

OPEN_IDS = {
    "clan": "tk:open:clan",
    "staff": "tk:open:staff",
    "report": "tk:open:report",
    "help": "tk:open:help",
}

IS_COMPONENTS_V2 = 32768


def _t(content):
    return {"type": 10, "content": content}


def _line():
    return {"type": 14, "spacing": 1, "divider": True}


def _gap():
    return {"type": 14, "spacing": 1, "divider": False}


def _box(*items):
    return {"type": 17, "components": list(items)}


def _code(text):
    return "```\n" + text + "\n```"


def _sub(emoji, title, body):
    return _t(f"## {EMOJI[emoji]} {title}\n{_code(body)}")


def build_rules_payload():
    """Общие правила сервера (без привязки к конкретной игре или клану)."""
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            _box(
                _t(f"# {EMOJI['rules']} Правила сервера"),
                _line(),
                _t(_code(
                    "Перед тем как писать и общаться, внимательно прочитайте правила. "
                    "Находясь на сервере, вы автоматически соглашаетесь с ними. "
                    "Незнание правил не освобождает от ответственности."
                )),
            ),
            _line(),
            _box(
                _t("#  ၊ Общие правила"),
                _line(),
                _sub("whiteheart", "Уважение и поведение",
                     "1.1 Относитесь ко всем участникам с уважением\n"
                     "1.2 Оскорбления, травля и провокации запрещены\n"
                     "1.3 Дискриминация и разжигание вражды запрещены\n"
                     "1.4 Конфликты решайте спокойно, при необходимости — через администрацию\n"
                     "1.5 Решения администрации не обсуждаются в общих каналах"),
                _gap(),
                _sub("whitebutterfly", "Чат и контент",
                     "2.1 Пишите в каналах строго по теме\n"
                     "2.2 Спам, флуд, капс и злоупотребление упоминаниями запрещены\n"
                     "2.3 Реклама сторонних серверов, сайтов и услуг запрещена\n"
                     "2.4 NSFW, шок-контент, угрозы и призывы к насилию запрещены\n"
                     "2.5 Чужие личные данные и переписки нельзя публиковать без согласия"),
            ),
            _line(),
            _box(
                _t("#  ၊၊ Голосовые каналы"),
                _line(),
                _sub("discord", "Правила войса",
                     "3.1 Не кричите и не создавайте шум, музыку и саундпад — только с согласия остальных\n"
                     "3.2 Запись разговоров без согласия участников запрещена\n"
                     "3.3 Не перемещайтесь между каналами, чтобы мешать другим\n"
                     "3.4 Выполняйте просьбы администрации и модераторов"),
            ),
            _line(),
            _box(
                _t("#  ၊၊၊ Безопасность"),
                _line(),
                _sub("blackbutterfly", "Защита аккаунта и сервера",
                     "4.1 Никому не передавайте пароли, токены и доступ к аккаунтам\n"
                     "4.2 Не переходите по подозрительным ссылкам и не скачивайте файлы от незнакомцев\n"
                     "4.3 Боты и вебхуки добавляются только с согласия администрации\n"
                     "4.4 О взломах, утечках и подозрительных участниках сообщайте администрации"),
            ),
            _line(),
            _box(
                _t("#  ɪᴠ Наказания"),
                _line(),
                _sub("greybutterfly", "Система наказаний",
                     "Предупреждение → Мут → Кик → Бан\n\n"
                     "5.1 Решение принимает администрация с учётом тяжести и повторности\n"
                     "5.2 Тяжёлые нарушения (угрозы, взломы, слив данных) — бан без предупреждения\n"
                     "5.3 Обход наказания с другого аккаунта ведёт к бану"),
                _gap(),
                _sub("boost", "Обжалование",
                     "6.1 Не согласны с наказанием — подайте жалобу через заявку\n"
                     "6.2 Правила могут меняться, следите за новостями сервера"),
            ),
        ],
    }


# (вид, эмодзи, заголовок, описание, подпись кнопки, стиль кнопки: 1 синяя, 2 серая, 3 зелёная, 4 красная)
PANEL_ITEMS = [
    ("clan", "grass", "Заявка в клан",
     "Хотите вступить в клан? Заполните короткую анкету.\nРассмотрят капитаны и шефы.",
     "В клан", 3),
    ("staff", "whitebutterfly", "Заявка в персонал",
     "Хотите помогать серверу? Расскажите об опыте и времени онлайн.",
     "В персонал", 1),
    ("report", "rules", "Жалоба",
     "Нарушение правил или конфликт? Опишите ситуацию и приложите доказательства.",
     "Жалоба", 4),
    ("help", "greybutterfly", "Обращение",
     "Есть вопрос или проблема? Опишите её, персонал свяжется с вами.\nЕсли нужно, откроют временный канал.",
     "Обращение", 2),
]


def build_panel_payload():
    sections = []
    buttons = []
    for i, (kind, emoji, title, desc, label, style) in enumerate(PANEL_ITEMS):
        if i:
            sections.append(_gap())
        sections.append(_sub(emoji, title, desc))
        buttons.append({
            "type": 2,
            "style": style,
            "label": label,
            "custom_id": OPEN_IDS[kind],
            "emoji": button_emoji(emoji),
        })
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            _box(
                _t(f"# {EMOJI['whiteheart']} Подача заявок"),
                _line(),
                _t(_code(
                    "Выберите тип обращения и заполните форму. Заявка уйдёт "
                    "ответственным на рассмотрение, при необходимости они создадут "
                    "с вами временный канал. Решение придёт в личные сообщения, "
                    "поэтому откройте их."
                )),
            ),
            _line(),
            _box(*sections, _line(), {"type": 1, "components": buttons}),
        ],
    }


DECISION_KINDS = ("clan", "staff")


def build_app_payload(*, kind, title, user_id, answers, status, color,
                      avatar_url=None, temp_channel_id=None, decided=False,
                      ping_role_id=None, ping=False):
    """Заявка для канала логов: заголовок, ответы (## вопрос + код-блок), кнопки."""
    header = f"# {EMOJI['whitebutterfly']} {title}\n**Заявитель:** <@{user_id}>\n**Статус:** {status}"
    if temp_channel_id:
        header += f"\n**Канал:** <#{temp_channel_id}>"
    if avatar_url:
        head = {
            "type": 9,
            "components": [_t(header)],
            "accessory": {"type": 11, "media": {"url": avatar_url}},
        }
    else:
        head = _t(header)

    items = [head, _line()]
    for i, (label, value) in enumerate(answers):
        if i:
            items.append(_gap())
        value = (str(value) or "—").replace("```", "ʼʼʼ")
        items.append(_sub("whitebutterfly", label, value or "—"))

    buttons = [{
        "type": 2, "style": 1, "label": "Взять на рассмотрение",
        "custom_id": "ap:claim", "disabled": decided,
    }, {
        "type": 2, "style": 3,
        "label": "Принять" if kind in DECISION_KINDS else "Решено",
        "custom_id": "ap:accept", "disabled": decided,
    }]
    if kind in DECISION_KINDS:
        buttons.append({
            "type": 2, "style": 4, "label": "Отклонить",
            "custom_id": "ap:reject", "disabled": decided,
        })
    buttons.append({
        "type": 2, "style": 2, "label": "Создать временный канал",
        "custom_id": "ap:temp",
    })
    items.extend([_line(), {"type": 1, "components": buttons}])

    components = []
    if ping_role_id:
        components.append(_t(f"<@&{ping_role_id}>"))
    components.append({"type": 17, "accent_color": color, "components": items})
    mentions = {"parse": []}
    if ping and ping_role_id:
        mentions["roles"] = [str(ping_role_id)]
    return {"flags": IS_COMPONENTS_V2, "components": components, "allowed_mentions": mentions}


def build_profile_payload(*, name, user_id, avatar_url, nick, first_join, squad,
                          violations, rewards, notes=None, color=0x2B2D31, stack=None):
    """Профиль участника. rewards: список (эмодзи, название); notes=None скрывает блок."""
    header = f"# {EMOJI['steve']} {name}\n<@{user_id}>"
    if avatar_url:
        head = {
            "type": 9,
            "components": [_t(header)],
            "accessory": {"type": 11, "media": {"url": avatar_url}},
        }
    else:
        head = _t(header)

    rewards_text = "\n".join(f"{emoji} {title}" for emoji, title in rewards[:20]) or "—"
    items = [
        head,
        _line(),
        _sub("whitebutterfly", "Ник в Minecraft", nick or "не указан"),
        _gap(),
        _sub("whitebutterfly", "Первый заход", first_join),
        _gap(),
        _sub("whitebutterfly", "Отряд", squad),
        _gap(),
        *([_sub("whitebutterfly", "Стак", stack), _gap()] if stack else []),
        _sub("whitebutterfly", "Нарушений", str(violations)),
        _gap(),
        _t(f"## {EMOJI['whitebutterfly']} Награды\n{rewards_text}"),
    ]
    if notes is not None:
        items.extend([_gap(), _sub("whitebutterfly", "Примечания", notes.replace("```", "ʼʼʼ") or "—")])
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": color, "components": items}],
        "allowed_mentions": {"parse": []},
    }


def build_report_payload(*, squad, week_text, captain_id, avatar_url, stats, answers, color=0x5865F2):
    """Отчёт капитана: автоматическая статистика + ответы (## вопрос и код-блок)."""
    header = (f"# {EMOJI['whitebutterfly']} Отчёт: {squad}\n"
              f"**Неделя:** {week_text}\n**Капитан:** <@{captain_id}>")
    if avatar_url:
        head = {"type": 9, "components": [_t(header)],
                "accessory": {"type": 11, "media": {"url": avatar_url}}}
    else:
        head = _t(header)
    items = [head, _line(), _sub("whitebutterfly", "Активность отряда", stats)]
    for label, value in answers:
        items.extend([_gap(), _sub("whitebutterfly", label, (value or "—").replace("```", "ʼʼʼ"))])
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": color, "components": items}],
        "allowed_mentions": {"parse": []},
    }


def _mentions_off():
    return {"parse": []}


def build_stacks_main_payload():
    """Главная панель стаков: создать, вступить, посмотреть свой."""
    buttons = [
        {"type": 2, "style": 3, "label": "Создать стак", "custom_id": "st:create"},
        {"type": 2, "style": 1, "label": "Вступить в стак", "custom_id": "st:join"},
        {"type": 2, "style": 2, "label": "Мой стак", "custom_id": "st:me"},
    ]
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            {"type": 17, "components": [
                _t(f"# {EMOJI['whitebutterfly']} Стаки"),
                _line(),
                _t(_code(
                    "Стак — ваша команда внутри сервера. Капитан создаёт стак и выбирает "
                    "название, бот создаёт роли, текстовый и голосовой каналы. Остальные "
                    "подают заявки, капитан их принимает. Один игрок состоит в одном стаке."
                )),
                _gap(),
                {"type": 1, "components": buttons},
            ]},
        ],
        "allowed_mentions": _mentions_off(),
    }


def build_stack_panel_payload(*, name, captain_id, members, max_members, applicants, description=""):
    """Панель в канале стака: состав и список заявок с выбором «Добавить в стак».

    applicants: список (user_id, подпись для меню); members: список user_id.
    """
    header = (f"# {EMOJI['whitebutterfly']} {name}\n**Капитан:** <@{captain_id}>\n"
              f"**Состав:** {len(members)}/{max_members}")
    items = [_t(header), _line()]
    if description:
        items.extend([_sub("whitebutterfly", "Описание", description.replace("```", "ʼʼʼ")), _gap()])
    items.append(_t(f"## {EMOJI['whitebutterfly']} Состав\n" +
                    ("\n".join(f"• <@{uid}>" for uid in members[:30]) or "—")))
    items.append(_gap())
    if applicants:
        apps_text = "\n".join(f"• <@{uid}>" for uid, _label in applicants[:30])
    else:
        apps_text = "Заявок пока нет."
    items.append(_t(f"## {EMOJI['whitebutterfly']} Добавить в стак\n{apps_text}"))

    def select(custom_id, placeholder):
        if applicants:
            options = [{"label": label[:100] or "участник", "value": str(uid)} for uid, label in applicants[:25]]
            disabled = False
        else:
            options = [{"label": "Нет заявок", "value": "0"}]
            disabled = True
        return {"type": 1, "components": [{
            "type": 3, "custom_id": custom_id, "placeholder": placeholder,
            "options": options, "min_values": 1, "max_values": 1, "disabled": disabled,
        }]}

    items.append(select("st:accept", "Добавить в стак"))
    items.append(select("st:decline", "Отклонить заявку"))
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "accent_color": 0x5865F2, "components": items}],
        "allowed_mentions": _mentions_off(),
    }


def build_stack_control_payload(*, name, guests_open, limit):
    """Панель капитана: войс (как в TempVoice) и управление составом."""
    state = "открыт для гостей" if guests_open else "только для стака"
    limit_text = str(limit) if limit else "без лимита"
    buttons = [
        {"type": 2, "style": 2, "label": "Лимит войса", "custom_id": "st:limit"},
        {"type": 2, "style": 2, "label": "Закрыть для гостей" if guests_open else "Открыть для гостей", "custom_id": "st:guests"},
        {"type": 2, "style": 1, "label": "Переименовать", "custom_id": "st:rename"},
        {"type": 2, "style": 4, "label": "Распустить", "custom_id": "st:disband"},
    ]

    def user_select(custom_id, placeholder):
        return {"type": 1, "components": [{
            "type": 5, "custom_id": custom_id, "placeholder": placeholder,
            "min_values": 1, "max_values": 1,
        }]}

    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "components": [
            _t(f"# {EMOJI['whitebutterfly']} Управление стаком"),
            _line(),
            _sub("whitebutterfly", "Голосовой канал",
                 f"Доступ: {state}\nЛимит: {limit_text}"),
            _gap(),
            {"type": 1, "components": buttons},
            user_select("st:permit", "Пустить в войс (гость)"),
            user_select("st:remove", "Исключить из стака"),
            user_select("st:transfer", "Передать капитана"),
        ]}],
        "allowed_mentions": _mentions_off(),
    }


def build_stack_list_payload(stacks, max_members):
    """Каталог стаков для канала. stacks: [{name, captain_id, count, description}]."""
    items = [_t(f"# {EMOJI['whitebutterfly']} Стаки сервера"), _line()]
    if not stacks:
        items.append(_t("Пока нет ни одного стака. Станьте первым."))
    blocks = []
    for st in stacks[:20]:
        text = (f"## {EMOJI['whitebutterfly']} {st['name']}\n"
                f"**Капитан:** <@{st['captain_id']}> · **Состав:** {st['count']}/{max_members}")
        if st.get("description"):
            text += "\n" + st["description"][:60].replace("```", "ʼʼʼ")
        blocks.append(text)
    # по три стака в одном текстовом блоке, чтобы не упереться в лимит компонентов
    for i in range(0, len(blocks), 3):
        if i:
            items.append(_gap())
        items.append(_t("\n\n".join(blocks[i:i + 3])))
    if len(stacks) > 20:
        items.append(_t(f"…и ещё {len(stacks) - 20}"))
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [{"type": 17, "components": items}],
        "allowed_mentions": _mentions_off(),
    }


def build_memo_payload(*, title, intro, total, squads, stacks, max_members, structure_text,
                       structure_code=False, banner_name=None, links=(), extra=()):
    """Памятка-обзор: отряды и капитаны, стаки, структура сервера, ссылки.

    squads: [{title, count, captains: [id]}]; stacks: [{name, captain_id, count}];
    links: [(подпись, url, имя_эмодзи|None)].
    """
    BF = EMOJI["whitebutterfly"]
    head = []
    if banner_name:
        head.extend([{"type": 12, "items": [{"media": {"url": f"attachment://{banner_name}"}}]}, _gap()])
    head.extend([_t(f"# {BF} {title}"), _line()])
    if intro:
        head.append(_t(_code(intro.replace("```", "ʼʼʼ"))))
    head.append(_t(f"**Участников:** {total}"))

    squad_lines = []
    for sq in squads:
        caps = ", ".join(f"<@{i}>" for i in sq["captains"][:2]) or "не назначен"
        squad_lines.append(f"• **{sq['title']}** · {sq['count']} чел. · капитан {caps}")
    squads_box = _box(_t("#  ၊ Отряды"), _line(), _t("\n".join(squad_lines) or "Отряды пока не созданы."))

    stack_lines = [f"• **{st['name']}** · <@{st['captain_id']}> · {st['count']}/{max_members}"
                   for st in stacks[:10]]
    if len(stacks) > 10:
        stack_lines.append(f"…и ещё {len(stacks) - 10}")
    stacks_box = _box(_t("#  ၊၊ Стаки"), _line(), _t("\n".join(stack_lines) or "Стаков пока нет. Создайте первый."))

    if structure_code:
        structure = _t(_code(structure_text.replace("```", "ʼʼʼ")))
    else:
        structure = _t(structure_text or "—")
    structure_box = _box(_t("#  ၊၊၊ Структура сервера"), _line(), structure)

    components = [_box(*head), _line(), squads_box, _line(), stacks_box, _line(), structure_box]
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
        rows = [{"type": 1, "components": buttons[i:i + 5]} for i in range(0, len(buttons), 5)]
        components.extend([_line(), _box(_t("#  ɪᴠ Разделы и ссылки"), _line(), *rows)])
    return {"flags": IS_COMPONENTS_V2, "components": components, "allowed_mentions": _mentions_off()}


def build_news_payload(title, text, media=None, plain=False):
    """Новость в общем стиле. media: ("image" | "file", имя_файла) или None."""
    text = text.replace("```", "ʼʼʼ")
    body = text if plain else _code(text)
    items = [_t(f"# {EMOJI['whitebutterfly']} {title}"), _line(), _t(body)]
    if media:
        kind, filename = media
        items.append(_gap())
        if kind == "image":
            items.append({"type": 12, "items": [{"media": {"url": f"attachment://{filename}"}}]})
        else:
            items.append({"type": 13, "file": {"url": f"attachment://{filename}"}})
    return {"flags": IS_COMPONENTS_V2, "components": [_box(*items)]}


async def post_components(bot, channel, payload, upload=None):
    """Отправляет сообщение Components V2 через API Discord.

    upload: (байты, имя_файла) если нужно приложить файл.
    """
    from discord import File
    from discord.http import Route

    route = Route("POST", "/channels/{channel_id}/messages", channel_id=channel.id)
    if upload is None:
        return await bot.http.request(route, json=payload)

    data, filename = upload
    payload = dict(payload)
    payload["attachments"] = [{"id": 0, "filename": filename}]
    file = File(io.BytesIO(data), filename=filename)
    form = [
        {
            "name": "payload_json",
            "value": json.dumps(payload, ensure_ascii=False),
            "content_type": "application/json",
        },
        {
            "name": "files[0]",
            "value": file.fp,
            "filename": filename,
            "content_type": "application/octet-stream",
        },
    ]
    return await bot.http.request(route, form=form, files=[file])


async def edit_components(bot, channel_id, message_id, payload, upload=None):
    """Редактирует сообщение Components V2 через API Discord (при необходимости с файлом)."""
    from discord import File
    from discord.http import Route

    route = Route(
        "PATCH", "/channels/{channel_id}/messages/{message_id}",
        channel_id=channel_id, message_id=message_id,
    )
    if upload is None:
        return await bot.http.request(route, json=payload)
    data, filename = upload
    payload = dict(payload)
    payload["attachments"] = [{"id": 0, "filename": filename}]
    file = File(io.BytesIO(data), filename=filename)
    form = [
        {"name": "payload_json", "value": json.dumps(payload, ensure_ascii=False),
         "content_type": "application/json"},
        {"name": "files[0]", "value": file.fp, "filename": filename,
         "content_type": "application/octet-stream"},
    ]
    return await bot.http.request(route, form=form, files=[file])
