"""Общие функции без зависимости от discord (чтобы их было легко проверять)."""
import re
import time
from datetime import datetime, timedelta, timezone

import db

ORDINALS = {
    1: "Первый", 2: "Второй", 3: "Третий", 4: "Четвёртый", 5: "Пятый",
    6: "Шестой", 7: "Седьмой", 8: "Восьмой", 9: "Девятый", 10: "Десятый",
}
_ROLE_RE = re.compile(r"(\d+)\s*group|group\s*(\d+)", re.I)


def squad_title(slot: int) -> str:
    return f"{ORDINALS.get(slot, f'{slot}-й')} отряд"


def squad_roles(guild):
    """Роли отрядов: заданные в /setting apps squads плюс найденные по названию «N group».

    Возвращает список (номер, роль), отсортированный по номеру.
    """
    found = {}
    stack_roles = db.get_stack_role_ids(guild.id)
    for slot, role_id in db.get_squads(guild.id):
        role = guild.get_role(role_id)
        if role:
            found[slot] = role
    for role in guild.roles:
        if role.is_default() or role.id in stack_roles:
            continue
        match = _ROLE_RE.search(role.name)
        if match:
            slot = int(match.group(1) or match.group(2))
            if 1 <= slot <= 25:
                found.setdefault(slot, role)
    return sorted(found.items())


def format_first_join(timestamp) -> str:
    if not timestamp:
        return "неизвестно"
    moment = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    days = max(0, int((time.time() - timestamp) // 86400))
    return f"{moment:%d.%m.%Y} ({days} дн. назад)"


def format_date(timestamp) -> str:
    if not timestamp:
        return ""
    return f"{datetime.fromtimestamp(timestamp, tz=timezone.utc):%d.%m.%Y}"


def parse_date(text: str):
    """«31.10.2026» -> метка времени (UTC). Пустая строка -> None. Ошибка -> ValueError."""
    text = text.strip()
    if not text:
        return None
    moment = datetime.strptime(text, "%d.%m.%Y").replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


# ---------- Права ----------
def can_manage(member) -> bool:
    """Админы и роли персонала (из /setting apps setup)."""
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    staff_roles = db.get_staff_role_ids(member.guild.id)
    return any(r.id in staff_roles for r in member.roles)


# ---------- Время и недели ----------
DEFAULT_OFFSET = 3  # по умолчанию МСК; меняется командой /setting report setup
ACTIVE_VOICE_SECONDS = 600  # 10 минут в войсе считаются активностью


def guild_offset(guild_id) -> int:
    settings = db.get_report_settings(guild_id)
    if settings and settings["utc_offset"] is not None:
        return settings["utc_offset"]
    return DEFAULT_OFFSET


def local_now(offset: int):
    return datetime.now(timezone.utc) + timedelta(hours=offset)


def day_key(moment) -> str:
    return moment.strftime("%Y-%m-%d")


def since_key(moment, days: int) -> str:
    """Ключ дня, с которого считаются последние `days` дней (включая сегодня)."""
    return day_key(moment - timedelta(days=days - 1))


def week_key(moment) -> str:
    iso = moment.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_start_key(moment) -> str:
    return day_key(moment - timedelta(days=moment.weekday()))


def week_range_text(moment) -> str:
    monday = moment - timedelta(days=moment.weekday())
    sunday = monday + timedelta(days=6)
    return f"{monday:%d.%m}–{sunday:%d.%m.%Y}"


def format_hours(seconds) -> str:
    return f"{seconds / 3600:.1f}"


def format_day(day) -> str:
    """«2026-10-31» -> «31.10.2026»; пусто -> «—»."""
    if not day:
        return "—"
    y, m, d = day.split("-")
    return f"{d}.{m}.{y}"


# ---------- Капитаны и статистика отрядов ----------
_CAPTAIN_RE = re.compile(r"captain|капитан", re.I)
_RU_ORD = (("перв", 1), ("втор", 2), ("трет", 3), ("четв", 4), ("пят", 5),
           ("шест", 6), ("седьм", 7), ("восьм", 8), ("девят", 9), ("десят", 10))


def slot_from_name(name: str):
    match = re.search(r"(\d+)", name)
    if match:
        return int(match.group(1))
    lowered = name.lower()
    for stem, number in _RU_ORD:
        if stem in lowered:
            return number
    return None


def captain_roles(guild):
    """Роли капитанов: любая роль со словом captain/капитан в названии. [(номер отряда|None, роль)]"""
    stack_roles = db.get_stack_role_ids(guild.id)
    return [
        (slot_from_name(role.name), role)
        for role in guild.roles
        if not role.is_default() and role.id not in stack_roles and _CAPTAIN_RE.search(role.name)
    ]


def find_captains(guild):
    """[(участник, номер отряда|None)] для всех, у кого есть роль капитана."""
    squads = dict(squad_roles(guild))
    found = {}
    for slot, role in captain_roles(guild):
        for member in role.members:
            if member.bot or member.id in found:
                continue
            own_slot = slot
            if own_slot is None:
                for squad_slot, squad_role in squads.items():
                    if squad_role in member.roles:
                        own_slot = squad_slot
                        break
            found[member.id] = (member, own_slot)
    return list(found.values())


def captain_slot_of(guild, member):
    """(капитан ли участник, номер его отряда|None)."""
    squads = dict(squad_roles(guild))
    for slot, role in captain_roles(guild):
        if role in member.roles:
            if slot is None:
                for squad_slot, squad_role in squads.items():
                    if squad_role in member.roles:
                        slot = squad_slot
                        break
            return True, slot
    return False, None


def squad_member_rows(guild, role, since_day):
    """Статистика участников роли отряда с since_day."""
    members = [m for m in role.members if not m.bot]
    totals = db.get_activity_totals(guild.id, [m.id for m in members], since_day)
    rows = []
    for member in members:
        messages, voice, last = totals.get(member.id, (0, 0, None))
        rows.append({
            "member": member, "messages": messages, "voice": voice, "last": last,
            "active": messages >= 1 or voice >= ACTIVE_VOICE_SECONDS,
        })
    return rows


def squad_summary(rows) -> str:
    total = len(rows)
    active = [r for r in rows if r["active"]]
    percent = round(100 * len(active) / total) if total else 0
    inactive = [r["member"].display_name for r in rows if not r["active"]]
    lines = [
        f"Участников: {total}",
        f"Активных: {len(active)} ({percent}%)",
        f"Сообщений: {sum(r['messages'] for r in rows)}",
        f"В войсе: {format_hours(sum(r['voice'] for r in rows))} ч",
    ]
    if inactive:
        lines.append("Неактивные: " + ", ".join(inactive[:15]) + ("…" if len(inactive) > 15 else ""))
    return "\n".join(lines)


# ---------- Стаки: названия и оформление ----------
STACK_PREFIX = "⚔〡・"
STACK_PALETTE = [0x58C58A, 0x4DA3FF, 0xFFB84D, 0xE2584D, 0xB06CFF, 0x4DD0E1, 0xFF7EB6, 0xA3D65C]
_NAME_RE = re.compile(r"^[\w .\-]{2,24}$")


def fancy(text: str) -> str:
    """Латиница -> курсив 𝘈𝘣𝘤, цифры -> полноширинные １２３ (как в названиях каналов отрядов)."""
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr(0x1D622 + ord(ch) - 97))
        elif "A" <= ch <= "Z":
            out.append(chr(0x1D608 + ord(ch) - 65))
        elif "0" <= ch <= "9":
            out.append(chr(0xFF10 + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)


def stack_text_channel_name(name: str) -> str:
    return (STACK_PREFIX + fancy(name).replace(" ", "-"))[:100]


def stack_voice_channel_name(name: str) -> str:
    return (STACK_PREFIX + fancy(name))[:100]


def clean_stack_name(raw: str) -> str:
    return " ".join(raw.split())


def valid_stack_name(name: str) -> bool:
    return bool(_NAME_RE.match(name)) and name[0] not in ".-" and name[-1] not in ".-"


def stack_color(name: str) -> int:
    return STACK_PALETTE[sum(ord(c) for c in name) % len(STACK_PALETTE)]


# ---------- Памятка-обзор ----------
def structure_auto(guild, limit: int = 1400) -> str:
    """Структура сервера для обычных участников: категории и каналы, которые видит @everyone."""
    everyone = guild.default_role
    blocks, total = [], 0
    for category in guild.categories:
        names = [f"<#{ch.id}>" for ch in category.channels if ch.permissions_for(everyone).view_channel]
        if not names:
            continue
        block = f"**{category.name}**\n" + " · ".join(names[:12])
        if total + len(block) + 2 > limit:
            blocks.append("…")
            break
        blocks.append(block)
        total += len(block) + 2
    return "\n\n".join(blocks)


def memo_overview(guild):
    """Данные для памятки: отряды с числом участников и капитанами, список стаков, лимит стака."""
    captains = {}
    for member, slot in find_captains(guild):
        if slot is not None:
            captains.setdefault(slot, []).append(member.id)
    squads = []
    for slot, role in squad_roles(guild):
        squads.append({
            "title": squad_title(slot),
            "count": sum(1 for m in role.members if not m.bot),
            "captains": captains.get(slot, []),
        })
    settings = db.get_stack_settings(guild.id) or {}
    stacks = [
        {"name": st["name"], "captain_id": st["captain_id"], "count": len(db.list_stack_members(st["id"]))}
        for st in db.list_stacks(guild.id)
    ]
    return squads, stacks, settings.get("max_members", 10)


# ---------- Тексты для кнопок памятки ----------
_DEFAULT_ROLE_TEXT = {
    "owner": "Владелец сервера. Принимает ключевые решения.",
    "main personal": "Второе лицо по серверу. Принимает отчёты шефов и следит за порядком.",
    "chef of discord": "Шеф Discord: настройка сервера, функционал и персонал.",
    "chef of gaming": "Шеф игровой деятельности: активность клана. Капитаны отчитываются ему.",
    "chef of media": "Шеф медиа: контент, ролики и оформление клана.",
    "head admin": "Старший администратор: руководит администрацией.",
    "administration": "Рабочее звено: следит за порядком и рассматривает заявки.",
    "гарант": "Проводит гарантированные сделки в канале «гарант».",
    "head moderator": "Старший модератор: руководит модераторами.",
    "moderator": "Следит за порядком в чатах и голосовых, выдаёт наказания по правилам.",
    "media": "Медиа-команда: помогает с контентом и оформлением.",
    "partner": "Партнёры сервера.",
    "vip": "Особый статус участников.",
    "staff": "Персонал сервера: доступ к служебным каналам.",
    "muted": "Наказание: нельзя писать и говорить, пока роль не снимут.",
    "eagles": "Принятые в клан. Выдаётся автоматически при принятии заявки.",
    "member": "Участник сервера. Даёт доступ к общим каналам.",
    "server booster": "Участники, которые бустят сервер.",
    "разборочная": "Доступ к закрытому каналу «разборочная».",
    "на рассмотрении": "Заявка подана, решение ещё не принято.",
}
_GROUP_TEXT = {
    "captains": "Капитаны отрядов: ведут отряд, принимают решения по нему и раз в неделю отчитываются шефам.",
    "squads": "Участники отрядов. Каждый состоит в одном отряде и подчиняется своему капитану.",
}
_ORDINAL_RE = re.compile(r"captain of the (\d+)\w* group", re.I)
_GROUP_RE = re.compile(r"^(\d+)\s*group$", re.I)


def _normalize_role_name(name: str) -> str:
    return " ".join(name.replace("・", " ").strip().casefold().split())


def default_role_description(name: str) -> str:
    """Стандартное описание роли по названию (без точки и лишних пробелов в начале)."""
    clean = _normalize_role_name(name)
    if clean in _DEFAULT_ROLE_TEXT:
        return _DEFAULT_ROLE_TEXT[clean]
    match = _ORDINAL_RE.search(clean)
    if match:
        return f"Капитан {match.group(1)} отряда."
    match = _GROUP_RE.match(clean)
    if match:
        return f"Участники {match.group(1)} отряда."
    return ""


def roles_structure_lines(guild, limit: int = 90):
    """Строки «роль · N чел. — кто это и зачем» сверху вниз по иерархии.

    Роли ботов, стаков и скрытые пропускаются, а роли капитанов отрядов и роли самих отрядов
    сводятся в одну строку каждая, чтобы список умещался в сообщение.
    """
    infos = db.get_role_infos(guild.id)
    stack_roles = db.get_stack_role_ids(guild.id)
    slots, groups = [], {"captains": [], "squads": []}
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role.is_default() or getattr(role, "managed", False) or role.id in stack_roles:
            continue
        info = infos.get(role.id, {})
        if info.get("hidden"):
            continue
        clean = _normalize_role_name(role.name)
        key = "captains" if _ORDINAL_RE.search(clean) else ("squads" if _GROUP_RE.match(clean) else None)
        if key:
            if not groups[key]:
                slots.append(key)
            groups[key].append(role)
            continue
        description = info.get("description") or default_role_description(role.name)
        count = sum(1 for m in role.members if not m.bot)
        line = f"<@&{role.id}> · {count} чел."
        if description:
            line += f" — {description[:limit]}"
        slots.append(line)
    lines = []
    for slot in slots:
        if slot in groups:
            roles = groups[slot]
            custom = next((infos[r.id]["description"] for r in roles if infos.get(r.id, {}).get("description")), "")
            count = sum(1 for r in roles for m in r.members if not m.bot)
            lines.append(
                " ".join(f"<@&{r.id}>" for r in roles) + f" · {count} чел. — "
                + (custom or _GROUP_TEXT[slot])[:max(limit, 140)]
            )
        else:
            lines.append(slot)
    return lines


def chunk_lines(lines, size: int = 3800):
    """Делит строки на части, чтобы каждая помещалась в описание эмбеда."""
    chunks, current, length = [], [], 0
    for line in lines:
        if current and length + len(line) + 1 > size:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


APPS_KIND_NAMES = (("clan", "В клан"), ("staff", "В персонал"), ("report", "Жалобы"), ("help", "Обращения"))


def apps_overview(guild):
    """Живая статистика заявок: (всего в обработке, строки по видам с ответственными)."""
    pending = db.count_pending_apps(guild.id)
    lines, total = [], 0
    for kind, label in APPS_KIND_NAMES:
        count, claimed = pending.get(kind, (0, 0))
        total += count
        cfg = db.get_ticket_config(guild.id, kind)
        who = f" · рассматривает <@&{cfg['staff_role_id']}>" if cfg and cfg.get("staff_role_id") else ""
        detail = f" (уже в работе: {claimed})" if count else ""
        lines.append(f"• {label}: **{count}**{detail}{who}")
    stack_requests = db.count_pending_stack_requests(guild.id)
    if stack_requests:
        lines.append(f"• Создание стаков: **{stack_requests}**")
        total += stack_requests
    return total, "\n".join(lines)


def fit_lines(lines, budget: int):
    """Оставляет строки, которые помещаются в budget символов, и дописывает «…и ещё N»."""
    out, length = [], 0
    for i, line in enumerate(lines):
        if length + len(line) + 1 > budget:
            out.append(f"…и ещё {len(lines) - i}")
            break
        out.append(line)
        length += len(line) + 1
    return out


# ---------- Временные голосовые: чистая логика ----------
VOICE_STATES = ("open", "locked", "private")


def voice_prefix(template: str) -> str:
    """Начало шаблона имени до «・» включительно (чтобы переименованные комнаты сохраняли стиль)."""
    return template.split("・")[0] + "・" if "・" in (template or "") else ""


def clean_room_name(prefix: str, text: str):
    """Название комнаты: пробелы приводятся в порядок, длина до 100. Пустое название -> None."""
    text = " ".join((text or "").split())
    if not text:
        return None
    return (prefix + text)[:100]


def voice_everyone_perms(state: str):
    """Права @everyone на комнату: (видит канал, может зайти)."""
    return {"open": (True, True), "locked": (True, False), "private": (False, False)}[state]


def voice_next_state(state: str) -> str:
    """Кнопка «Доступ» переключает: открыта -> закрыта -> приватная -> открыта."""
    return VOICE_STATES[(VOICE_STATES.index(state) + 1) % len(VOICE_STATES)]


def rename_allowed(times, now: float, limit: int = 2, window: int = 600):
    """Discord разрешает переименовывать канал ~2 раза за 10 минут. -> (можно, новый список, сколько ждать, сек)."""
    recent = [t for t in times if now - t < window]
    if len(recent) < limit:
        return True, recent, 0
    return False, recent, int(window - (now - recent[0])) + 1
