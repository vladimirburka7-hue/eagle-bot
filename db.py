"""Хранение настроек, входов и заявок (SQLite)."""
import sqlite3

conn = sqlite3.connect("bot.db")
conn.row_factory = sqlite3.Row
conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS settings (
        guild_id INTEGER PRIMARY KEY,
        welcome_channel INTEGER,
        log_channel INTEGER
    );
    CREATE TABLE IF NOT EXISTS joins (
        guild_id INTEGER, user_id INTEGER, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS ticket_config (
        guild_id INTEGER, kind TEXT,
        category_id INTEGER, staff_role_id INTEGER,
        log_channel_id INTEGER, accept_role_id INTEGER,
        PRIMARY KEY (guild_id, kind)
    );
    CREATE TABLE IF NOT EXISTS tickets (
        channel_id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        result TEXT NOT NULL DEFAULT 'pending',
        claimed_by INTEGER, intro_id INTEGER, app_message_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS apps (
        message_id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, kind TEXT NOT NULL,
        result TEXT NOT NULL DEFAULT 'pending',
        claimed_by INTEGER, decided_by INTEGER, temp_channel_id INTEGER,
        answers TEXT, avatar_url TEXT
    );
    CREATE TABLE IF NOT EXISTS ticket_counters (
        guild_id INTEGER, kind TEXT, n INTEGER NOT NULL,
        PRIMARY KEY (guild_id, kind)
    );
    CREATE TABLE IF NOT EXISTS profiles (
        guild_id INTEGER, user_id INTEGER,
        mc_nick TEXT, notes TEXT, first_joined_at INTEGER,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS reward_defs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL,
        UNIQUE (guild_id, name)
    );
    CREATE TABLE IF NOT EXISTS user_rewards (
        guild_id INTEGER, user_id INTEGER, reward_id INTEGER,
        given_by INTEGER, given_at INTEGER,
        PRIMARY KEY (guild_id, user_id, reward_id)
    );
    CREATE TABLE IF NOT EXISTS violations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        reason TEXT NOT NULL, moderator_id INTEGER, created_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS activity (
        guild_id INTEGER, user_id INTEGER, day TEXT,
        messages INTEGER NOT NULL DEFAULT 0,
        voice_seconds INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, day)
    );
    CREATE TABLE IF NOT EXISTS report_settings (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER, weekday INTEGER, hour INTEGER,
        utc_offset INTEGER, last_reminded TEXT
    );
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        squad_slot INTEGER, week TEXT NOT NULL, created_at INTEGER,
        channel_id INTEGER, message_id INTEGER,
        UNIQUE (guild_id, user_id, week)
    );
    CREATE TABLE IF NOT EXISTS stack_settings (
        guild_id INTEGER PRIMARY KEY,
        category_id INTEGER, review_channel_id INTEGER,
        list_channel_id INTEGER, list_message_id INTEGER,
        max_members INTEGER NOT NULL DEFAULT 10,
        max_stacks INTEGER NOT NULL DEFAULT 20,
        auto_approve INTEGER NOT NULL DEFAULT 0,
        role_anchor_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS stacks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT,
        captain_id INTEGER NOT NULL,
        role_id INTEGER, captain_role_id INTEGER,
        text_channel_id INTEGER, voice_channel_id INTEGER,
        panel_message_id INTEGER, control_message_id INTEGER,
        created_at INTEGER, renamed_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS stack_members (
        stack_id INTEGER, user_id INTEGER, joined_at INTEGER,
        PRIMARY KEY (stack_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS stack_apps (
        stack_id INTEGER, user_id INTEGER, created_at INTEGER,
        PRIMARY KEY (stack_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS stack_requests (
        message_id INTEGER PRIMARY KEY,
        channel_id INTEGER, guild_id INTEGER, user_id INTEGER,
        name TEXT, description TEXT, status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS memo_settings (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER, message_id INTEGER,
        rules_channel_id INTEGER, apps_channel_id INTEGER, stacks_channel_id INTEGER,
        title TEXT, intro TEXT, structure TEXT,
        banner BLOB, banner_name TEXT,
        activities TEXT, buttons_seeded INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS memo_buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, position INTEGER NOT NULL,
        label TEXT NOT NULL, emoji TEXT, style INTEGER NOT NULL DEFAULT 2,
        url TEXT, title TEXT, content TEXT, image BLOB, image_name TEXT,
        kind TEXT NOT NULL DEFAULT 'text'
    );
    CREATE TABLE IF NOT EXISTS role_info (
        guild_id INTEGER, role_id INTEGER,
        description TEXT, hidden INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, role_id)
    );
    CREATE TABLE IF NOT EXISTS flags (name TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS market_settings (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER, guarantor_channel_id INTEGER,
        max_active INTEGER NOT NULL DEFAULT 3,
        days INTEGER NOT NULL DEFAULT 7,
        rules_text TEXT, guide_channel_id INTEGER, guide_message_id INTEGER,
        guarantor_role_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS market_listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL,
        price TEXT, description TEXT, notes TEXT, contact TEXT, avatar_url TEXT,
        channel_id INTEGER, message_id INTEGER,
        created_at INTEGER, expires_at INTEGER,
        status TEXT NOT NULL DEFAULT 'open', closed_reason TEXT,
        bumped_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS market_responses (
        listing_id INTEGER, user_id INTEGER, message TEXT, created_at INTEGER,
        PRIMARY KEY (listing_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS voice_settings (
        guild_id INTEGER PRIMARY KEY,
        trigger_channel_id INTEGER, category_id INTEGER, name_template TEXT
    );
    CREATE TABLE IF NOT EXISTS voice_rooms (
        channel_id INTEGER PRIMARY KEY,
        guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'open', panel_message_id INTEGER, created_at INTEGER
    );
    CREATE TABLE IF NOT EXISTS voice_prefs (
        guild_id INTEGER, user_id INTEGER,
        name TEXT, user_limit INTEGER, state TEXT,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS automod_settings (
        guild_id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 1,
        log_channel_id INTEGER, center_channel_id INTEGER, center_message_id INTEGER,
        mute_minutes INTEGER NOT NULL DEFAULT 15,
        delete_messages INTEGER NOT NULL DEFAULT 1,
        include_mild INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS automod_words (
        guild_id INTEGER, word TEXT, kind TEXT,
        PRIMARY KEY (guild_id, word, kind)
    );
    CREATE TABLE IF NOT EXISTS automod_exempt (
        guild_id INTEGER, target_id INTEGER, kind TEXT,
        PRIMARY KEY (guild_id, target_id, kind)
    );
    CREATE TABLE IF NOT EXISTS mod_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER,
        action TEXT NOT NULL, minutes INTEGER, reason TEXT, source TEXT NOT NULL DEFAULT 'manual',
        category TEXT, word TEXT, excerpt TEXT, channel_id INTEGER, message_id INTEGER,
        created_at INTEGER, until INTEGER, status TEXT NOT NULL DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS squads (
        guild_id INTEGER, slot INTEGER, role_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, slot)
    );
    """
)
for _stmt in (  # миграция старой базы
    "ALTER TABLE tickets ADD COLUMN app_message_id INTEGER",
    "ALTER TABLE apps ADD COLUMN answers TEXT",
    "ALTER TABLE apps ADD COLUMN avatar_url TEXT",
    "ALTER TABLE stack_settings ADD COLUMN role_anchor_id INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN activities TEXT",
    "ALTER TABLE memo_settings ADD COLUMN buttons_seeded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE memo_buttons ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'",
    "ALTER TABLE market_settings ADD COLUMN rules_text TEXT",
    "ALTER TABLE market_settings ADD COLUMN guide_channel_id INTEGER",
    "ALTER TABLE market_settings ADD COLUMN guide_message_id INTEGER",
    "ALTER TABLE market_listings ADD COLUMN bumped_at INTEGER",
    "ALTER TABLE market_settings ADD COLUMN guarantor_role_id INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN show_squads INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN show_stacks INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN show_structure INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN show_members INTEGER",
    "ALTER TABLE memo_settings ADD COLUMN squads_title TEXT",
    "ALTER TABLE memo_settings ADD COLUMN stacks_title TEXT",
    "ALTER TABLE memo_settings ADD COLUMN structure_title TEXT",
    "ALTER TABLE memo_settings ADD COLUMN buttons_title TEXT",
    "ALTER TABLE memo_settings ADD COLUMN activities_title TEXT",
    "ALTER TABLE automod_settings ADD COLUMN off_categories TEXT",
):
    try:
        conn.execute(_stmt)
    except sqlite3.OperationalError:
        pass
conn.commit()

SETTING_NAMES = {"welcome_channel", "log_channel"}
TICKET_FIELDS = {"status", "result", "claimed_by", "intro_id"}
APP_FIELDS = {"result", "claimed_by", "decided_by", "temp_channel_id"}


def get_setting(guild_id, name):
    assert name in SETTING_NAMES
    row = conn.execute(
        f"SELECT {name} FROM settings WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    return row[0] if row else None


def set_setting(guild_id, name, value):
    assert name in SETTING_NAMES
    conn.execute(
        f"INSERT INTO settings (guild_id, {name}) VALUES (?, ?) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {name} = excluded.{name}",
        (guild_id, value),
    )
    conn.commit()


def register_join(guild_id, user_id):
    """Записывает вход, возвращает число входов ДО этого."""
    row = conn.execute(
        "SELECT count FROM joins WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    previous = row[0] if row else 0
    conn.execute(
        "INSERT INTO joins (guild_id, user_id, count) VALUES (?, ?, 1) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET count = count + 1",
        (guild_id, user_id),
    )
    conn.commit()
    return previous


def set_ticket_config(guild_id, kind, category_id, staff_role_id, log_channel_id, accept_role_id):
    conn.execute(
        "INSERT INTO ticket_config "
        "(guild_id, kind, category_id, staff_role_id, log_channel_id, accept_role_id) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, kind) DO UPDATE SET "
        "category_id = excluded.category_id, staff_role_id = excluded.staff_role_id, "
        "log_channel_id = excluded.log_channel_id, accept_role_id = excluded.accept_role_id",
        (guild_id, kind, category_id, staff_role_id, log_channel_id, accept_role_id),
    )
    conn.commit()


def get_ticket_config(guild_id, kind):
    row = conn.execute(
        "SELECT * FROM ticket_config WHERE guild_id = ? AND kind = ?",
        (guild_id, kind),
    ).fetchone()
    return dict(row) if row else None


def create_ticket(channel_id, guild_id, user_id, kind, app_message_id=None):
    conn.execute(
        "INSERT INTO tickets (channel_id, guild_id, user_id, kind, app_message_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (channel_id, guild_id, user_id, kind, app_message_id),
    )
    conn.commit()


def get_ticket(channel_id):
    row = conn.execute(
        "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return dict(row) if row else None


def find_open_ticket(guild_id, user_id, kind):
    row = conn.execute(
        "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND kind = ? "
        "AND status = 'open'",
        (guild_id, user_id, kind),
    ).fetchone()
    return dict(row) if row else None


def update_ticket(channel_id, **fields):
    assert fields and set(fields) <= TICKET_FIELDS
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE tickets SET {assignments} WHERE channel_id = ?",
        (*fields.values(), channel_id),
    )
    conn.commit()


def next_ticket_number(guild_id, kind):
    """Следующий номер заявки данного вида на сервере: 1, 2, 3..."""
    conn.execute(
        "INSERT INTO ticket_counters (guild_id, kind, n) VALUES (?, ?, 1) "
        "ON CONFLICT(guild_id, kind) DO UPDATE SET n = n + 1",
        (guild_id, kind),
    )
    row = conn.execute(
        "SELECT n FROM ticket_counters WHERE guild_id = ? AND kind = ?",
        (guild_id, kind),
    ).fetchone()
    conn.commit()
    return row[0]


def set_squads(guild_id, role_ids):
    """Полностью заменяет список отрядов: role_ids идут как отряды 1, 2, 3..."""
    conn.execute("DELETE FROM squads WHERE guild_id = ?", (guild_id,))
    for slot, role_id in enumerate(role_ids, start=1):
        conn.execute(
            "INSERT INTO squads (guild_id, slot, role_id) VALUES (?, ?, ?)",
            (guild_id, slot, role_id),
        )
    conn.commit()


def get_squads(guild_id):
    rows = conn.execute(
        "SELECT slot, role_id FROM squads WHERE guild_id = ? ORDER BY slot",
        (guild_id,),
    ).fetchall()
    return [(r["slot"], r["role_id"]) for r in rows]


def create_app(message_id, channel_id, guild_id, user_id, kind, answers_json="[]", avatar_url=None):
    conn.execute(
        "INSERT INTO apps (message_id, channel_id, guild_id, user_id, kind, answers, avatar_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message_id, channel_id, guild_id, user_id, kind, answers_json, avatar_url),
    )
    conn.commit()


def get_app(message_id):
    row = conn.execute(
        "SELECT * FROM apps WHERE message_id = ?", (message_id,)
    ).fetchone()
    return dict(row) if row else None


def find_pending_app(guild_id, user_id, kind):
    row = conn.execute(
        "SELECT * FROM apps WHERE guild_id = ? AND user_id = ? AND kind = ? "
        "AND result = 'pending'",
        (guild_id, user_id, kind),
    ).fetchone()
    return dict(row) if row else None


def update_app(message_id, **fields):
    assert fields and set(fields) <= APP_FIELDS
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE apps SET {assignments} WHERE message_id = ?",
        (*fields.values(), message_id),
    )
    conn.commit()


# ---------- Профили, награды, нарушения ----------
PROFILE_FIELDS = {"mc_nick", "notes", "first_joined_at"}


def get_staff_role_ids(guild_id):
    rows = conn.execute(
        "SELECT DISTINCT staff_role_id FROM ticket_config WHERE guild_id = ?",
        (guild_id,),
    ).fetchall()
    return {r[0] for r in rows if r[0]}


def get_profile(guild_id, user_id):
    row = conn.execute(
        "SELECT * FROM profiles WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def set_profile(guild_id, user_id, **fields):
    assert fields and set(fields) <= PROFILE_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO profiles (guild_id, user_id, {names}) VALUES (?, ?, {marks}) "
        f"ON CONFLICT(guild_id, user_id) DO UPDATE SET {updates}",
        (guild_id, user_id, *fields.values()),
    )
    conn.commit()


def set_first_join(guild_id, user_id, timestamp):
    """Запоминает самый первый заход (повторные входы не перезаписывают)."""
    conn.execute(
        "INSERT INTO profiles (guild_id, user_id, first_joined_at) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "first_joined_at = COALESCE(first_joined_at, excluded.first_joined_at)",
        (guild_id, user_id, timestamp),
    )
    conn.commit()


def find_user_by_nick(guild_id, nick):
    row = conn.execute(
        "SELECT user_id FROM profiles WHERE guild_id = ? AND mc_nick = ? COLLATE NOCASE",
        (guild_id, nick),
    ).fetchone()
    return row[0] if row else None


def add_reward_def(guild_id, name, emoji):
    """Создаёт награду. Возвращает id или None, если такое название уже есть."""
    if get_reward_def(guild_id, name):
        return None
    try:
        cur = conn.execute(
            "INSERT INTO reward_defs (guild_id, name, emoji) VALUES (?, ?, ?)",
            (guild_id, name, emoji),
        )
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid


def list_reward_defs(guild_id):
    rows = conn.execute(
        "SELECT * FROM reward_defs WHERE guild_id = ? ORDER BY id", (guild_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_reward_def(guild_id, name):
    """Ищет награду по названию без учёта регистра (в том числе для кириллицы)."""
    wanted = name.strip().casefold()
    for definition in list_reward_defs(guild_id):
        if definition["name"].casefold() == wanted:
            return definition
    return None


def delete_reward_def(guild_id, reward_id):
    conn.execute("DELETE FROM user_rewards WHERE guild_id = ? AND reward_id = ?", (guild_id, reward_id))
    conn.execute("DELETE FROM reward_defs WHERE guild_id = ? AND id = ?", (guild_id, reward_id))
    conn.commit()


def give_reward(guild_id, user_id, reward_id, given_by, timestamp):
    """Выдаёт награду. False, если она у человека уже есть."""
    try:
        conn.execute(
            "INSERT INTO user_rewards (guild_id, user_id, reward_id, given_by, given_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, reward_id, given_by, timestamp),
        )
    except sqlite3.IntegrityError:
        return False
    conn.commit()
    return True


def take_reward(guild_id, user_id, reward_id):
    cur = conn.execute(
        "DELETE FROM user_rewards WHERE guild_id = ? AND user_id = ? AND reward_id = ?",
        (guild_id, user_id, reward_id),
    )
    conn.commit()
    return cur.rowcount > 0


def get_user_rewards(guild_id, user_id):
    rows = conn.execute(
        "SELECT d.* FROM user_rewards u JOIN reward_defs d ON d.id = u.reward_id "
        "WHERE u.guild_id = ? AND u.user_id = ? ORDER BY u.given_at",
        (guild_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


def add_violation(guild_id, user_id, reason, moderator_id, timestamp):
    cur = conn.execute(
        "INSERT INTO violations (guild_id, user_id, reason, moderator_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (guild_id, user_id, reason, moderator_id, timestamp),
    )
    conn.commit()
    return cur.lastrowid


def list_violations(guild_id, user_id):
    rows = conn.execute(
        "SELECT * FROM violations WHERE guild_id = ? AND user_id = ? ORDER BY id",
        (guild_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_violation(guild_id, violation_id):
    cur = conn.execute(
        "DELETE FROM violations WHERE guild_id = ? AND id = ?", (guild_id, violation_id)
    )
    conn.commit()
    return cur.rowcount > 0


# ---------- Активность ----------
def add_activity(guild_id, user_id, day, messages=0, voice_seconds=0):
    conn.execute(
        "INSERT INTO activity (guild_id, user_id, day, messages, voice_seconds) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id, day) DO UPDATE SET "
        "messages = messages + excluded.messages, "
        "voice_seconds = voice_seconds + excluded.voice_seconds",
        (guild_id, user_id, day, messages, voice_seconds),
    )
    conn.commit()


def get_activity_totals(guild_id, user_ids, since_day):
    """{user_id: (сообщений, секунд в войсе, последний день активности)} начиная с since_day."""
    user_ids = list(user_ids)
    result = {}
    for i in range(0, len(user_ids), 500):
        chunk = user_ids[i:i + 500]
        marks = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT user_id, SUM(messages), SUM(voice_seconds), "
            f"MAX(CASE WHEN messages > 0 OR voice_seconds > 0 THEN day END) "
            f"FROM activity WHERE guild_id = ? AND day >= ? AND user_id IN ({marks}) "
            f"GROUP BY user_id",
            (guild_id, since_day, *chunk),
        ).fetchall()
        for r in rows:
            result[r[0]] = (r[1] or 0, r[2] or 0, r[3])
    return result


def get_last_active(guild_id, user_id):
    row = conn.execute(
        "SELECT MAX(day) FROM activity WHERE guild_id = ? AND user_id = ? "
        "AND (messages > 0 OR voice_seconds > 0)",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else None


def get_top_activity(guild_id, since_day, limit=10):
    rows = conn.execute(
        "SELECT user_id, SUM(messages) AS m, SUM(voice_seconds) AS v FROM activity "
        "WHERE guild_id = ? AND day >= ? GROUP BY user_id "
        "ORDER BY (SUM(messages) + SUM(voice_seconds) / 60) DESC LIMIT ?",
        (guild_id, since_day, limit),
    ).fetchall()
    return [(r[0], r[1] or 0, r[2] or 0) for r in rows]


# ---------- Отчёты капитанов ----------
def set_report_settings(guild_id, channel_id, weekday, hour, utc_offset):
    conn.execute(
        "INSERT INTO report_settings (guild_id, channel_id, weekday, hour, utc_offset) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id, "
        "weekday = excluded.weekday, hour = excluded.hour, utc_offset = excluded.utc_offset",
        (guild_id, channel_id, weekday, hour, utc_offset),
    )
    conn.commit()


def get_report_settings(guild_id):
    row = conn.execute(
        "SELECT * FROM report_settings WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    return dict(row) if row else None


def list_report_settings():
    return [dict(r) for r in conn.execute("SELECT * FROM report_settings").fetchall()]


def mark_reminded(guild_id, week):
    conn.execute(
        "UPDATE report_settings SET last_reminded = ? WHERE guild_id = ?", (week, guild_id)
    )
    conn.commit()


def get_report(guild_id, user_id, week):
    row = conn.execute(
        "SELECT * FROM reports WHERE guild_id = ? AND user_id = ? AND week = ?",
        (guild_id, user_id, week),
    ).fetchone()
    return dict(row) if row else None


def save_report(guild_id, user_id, squad_slot, week, created_at, channel_id, message_id):
    conn.execute(
        "INSERT INTO reports (guild_id, user_id, squad_slot, week, created_at, channel_id, message_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id, week) DO UPDATE SET squad_slot = excluded.squad_slot, "
        "created_at = excluded.created_at, channel_id = excluded.channel_id, "
        "message_id = excluded.message_id",
        (guild_id, user_id, squad_slot, week, created_at, channel_id, message_id),
    )
    conn.commit()


def list_reports_for_week(guild_id, week):
    rows = conn.execute(
        "SELECT * FROM reports WHERE guild_id = ? AND week = ?", (guild_id, week)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Стаки ----------
STACK_SETTING_FIELDS = {"category_id", "review_channel_id", "list_channel_id", "list_message_id",
                        "max_members", "max_stacks", "auto_approve", "role_anchor_id"}
STACK_FIELDS = {"name", "description", "captain_id", "role_id", "captain_role_id", "text_channel_id",
                "voice_channel_id", "panel_message_id", "control_message_id", "renamed_at"}


def get_stack_settings(guild_id):
    row = conn.execute("SELECT * FROM stack_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_stack_settings(guild_id, **fields):
    assert fields and set(fields) <= STACK_SETTING_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO stack_settings (guild_id, {names}) VALUES (?, {marks}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *fields.values()),
    )
    conn.commit()


def create_stack(guild_id, name, description, captain_id, created_at):
    cur = conn.execute(
        "INSERT INTO stacks (guild_id, name, description, captain_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild_id, name, description, captain_id, created_at),
    )
    conn.commit()
    return cur.lastrowid


def get_stack(stack_id):
    row = conn.execute("SELECT * FROM stacks WHERE id = ?", (stack_id,)).fetchone()
    return dict(row) if row else None


def get_stack_by_channel(channel_id):
    row = conn.execute("SELECT * FROM stacks WHERE text_channel_id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def list_stacks(guild_id):
    rows = conn.execute("SELECT * FROM stacks WHERE guild_id = ? ORDER BY id", (guild_id,)).fetchall()
    return [dict(r) for r in rows]


def find_stack_by_name(guild_id, name):
    """Поиск по названию без учёта регистра (в том числе кириллицы)."""
    wanted = name.strip().casefold()
    for stack in list_stacks(guild_id):
        if stack["name"].casefold() == wanted:
            return stack
    return None


def update_stack(stack_id, **fields):
    assert fields and set(fields) <= STACK_FIELDS
    assignments = ", ".join(f"{n} = ?" for n in fields)
    conn.execute(f"UPDATE stacks SET {assignments} WHERE id = ?", (*fields.values(), stack_id))
    conn.commit()


def delete_stack(stack_id):
    conn.execute("DELETE FROM stack_members WHERE stack_id = ?", (stack_id,))
    conn.execute("DELETE FROM stack_apps WHERE stack_id = ?", (stack_id,))
    conn.execute("DELETE FROM stacks WHERE id = ?", (stack_id,))
    conn.commit()


def add_stack_member(stack_id, user_id, joined_at):
    conn.execute(
        "INSERT OR IGNORE INTO stack_members (stack_id, user_id, joined_at) VALUES (?, ?, ?)",
        (stack_id, user_id, joined_at),
    )
    conn.commit()


def remove_stack_member(stack_id, user_id):
    conn.execute("DELETE FROM stack_members WHERE stack_id = ? AND user_id = ?", (stack_id, user_id))
    conn.commit()


def list_stack_members(stack_id):
    rows = conn.execute(
        "SELECT user_id FROM stack_members WHERE stack_id = ? ORDER BY joined_at, user_id", (stack_id,)
    ).fetchall()
    return [r[0] for r in rows]


def get_member_stack(guild_id, user_id):
    row = conn.execute(
        "SELECT s.* FROM stacks s JOIN stack_members m ON m.stack_id = s.id "
        "WHERE s.guild_id = ? AND m.user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def get_stack_role_ids(guild_id):
    """ID всех ролей стаков (общие и капитанские), чтобы не путать их с отрядами."""
    ids = set()
    for r in conn.execute("SELECT role_id, captain_role_id FROM stacks WHERE guild_id = ?", (guild_id,)):
        ids.update(x for x in r if x)
    return ids


def add_stack_app(stack_id, user_id, created_at):
    conn.execute(
        "INSERT OR IGNORE INTO stack_apps (stack_id, user_id, created_at) VALUES (?, ?, ?)",
        (stack_id, user_id, created_at),
    )
    conn.commit()


def get_stack_app(stack_id, user_id):
    row = conn.execute(
        "SELECT * FROM stack_apps WHERE stack_id = ? AND user_id = ?", (stack_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def list_stack_apps(stack_id):
    rows = conn.execute(
        "SELECT user_id, created_at FROM stack_apps WHERE stack_id = ? ORDER BY created_at", (stack_id,)
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def remove_stack_app(stack_id, user_id):
    conn.execute("DELETE FROM stack_apps WHERE stack_id = ? AND user_id = ?", (stack_id, user_id))
    conn.commit()


def remove_user_apps(guild_id, user_id):
    """Удаляет все заявки человека в стаки сервера. Возвращает id затронутых стаков."""
    rows = conn.execute(
        "SELECT a.stack_id FROM stack_apps a JOIN stacks s ON s.id = a.stack_id "
        "WHERE s.guild_id = ? AND a.user_id = ?",
        (guild_id, user_id),
    ).fetchall()
    ids = [r[0] for r in rows]
    for stack_id in ids:
        conn.execute("DELETE FROM stack_apps WHERE stack_id = ? AND user_id = ?", (stack_id, user_id))
    conn.commit()
    return ids


def create_stack_request(message_id, channel_id, guild_id, user_id, name, description):
    conn.execute(
        "INSERT INTO stack_requests (message_id, channel_id, guild_id, user_id, name, description) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, channel_id, guild_id, user_id, name, description),
    )
    conn.commit()


def get_stack_request(message_id):
    row = conn.execute("SELECT * FROM stack_requests WHERE message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def find_pending_stack_request(guild_id, user_id):
    row = conn.execute(
        "SELECT * FROM stack_requests WHERE guild_id = ? AND user_id = ? AND status = 'pending'",
        (guild_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def set_stack_request_status(message_id, status):
    conn.execute("UPDATE stack_requests SET status = ? WHERE message_id = ?", (status, message_id))
    conn.commit()


# ---------- Памятка-обзор сервера ----------
MEMO_FIELDS = {"channel_id", "message_id", "rules_channel_id", "apps_channel_id", "stacks_channel_id",
               "title", "intro", "structure", "banner", "banner_name", "activities", "buttons_seeded",
               "show_squads", "show_stacks", "show_structure", "show_members",
               "squads_title", "stacks_title", "structure_title", "buttons_title", "activities_title"}


def get_memo(guild_id):
    row = conn.execute("SELECT * FROM memo_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_memo(guild_id, **fields):
    assert fields and set(fields) <= MEMO_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO memo_settings (guild_id, {names}) VALUES (?, {marks}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *fields.values()),
    )
    conn.commit()


def list_memos():
    return [dict(r) for r in conn.execute(
        "SELECT * FROM memo_settings WHERE channel_id IS NOT NULL AND message_id IS NOT NULL"
    ).fetchall()]


# ---------- Кнопки памятки ----------
BUTTON_FIELDS = {"label", "emoji", "style", "url", "title", "content", "image", "image_name", "kind"}


def add_memo_button(guild_id, **fields):
    assert fields and set(fields) <= BUTTON_FIELDS
    row = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM memo_buttons WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    cur = conn.execute(
        f"INSERT INTO memo_buttons (guild_id, position, {names}) VALUES (?, ?, {marks})",
        (guild_id, row[0] + 1, *fields.values()),
    )
    conn.commit()
    return cur.lastrowid


def list_memo_buttons(guild_id):
    rows = conn.execute(
        "SELECT * FROM memo_buttons WHERE guild_id = ? ORDER BY position, id", (guild_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_memo_button(button_id):
    row = conn.execute("SELECT * FROM memo_buttons WHERE id = ?", (button_id,)).fetchone()
    return dict(row) if row else None


def update_memo_button(button_id, **fields):
    assert fields and set(fields) <= BUTTON_FIELDS
    assignments = ", ".join(f"{n} = ?" for n in fields)
    conn.execute(f"UPDATE memo_buttons SET {assignments} WHERE id = ?", (*fields.values(), button_id))
    conn.commit()


def delete_memo_button(button_id):
    conn.execute("DELETE FROM memo_buttons WHERE id = ?", (button_id,))
    conn.commit()


def move_memo_button(guild_id, button_id, new_position):
    """Ставит кнопку на новое место (1 = первая) и перенумеровывает остальные."""
    buttons = [b["id"] for b in list_memo_buttons(guild_id)]
    if button_id not in buttons:
        return False
    buttons.remove(button_id)
    buttons.insert(max(0, min(len(buttons), new_position - 1)), button_id)
    for position, bid in enumerate(buttons, start=1):
        conn.execute("UPDATE memo_buttons SET position = ? WHERE id = ?", (position, bid))
    conn.commit()
    return True


# ---------- Статистика заявок и описания ролей ----------
def count_pending_apps(guild_id):
    """{вид: (на рассмотрении, из них взято в работу)} по заявкам, ожидающим решения."""
    rows = conn.execute(
        "SELECT kind, COUNT(*), SUM(CASE WHEN claimed_by IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM apps WHERE guild_id = ? AND result = 'pending' GROUP BY kind",
        (guild_id,),
    ).fetchall()
    return {r[0]: (r[1], r[2] or 0) for r in rows}


def count_pending_stack_requests(guild_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM stack_requests WHERE guild_id = ? AND status = 'pending'", (guild_id,)
    ).fetchone()
    return row[0]


def get_role_infos(guild_id):
    rows = conn.execute("SELECT * FROM role_info WHERE guild_id = ?", (guild_id,)).fetchall()
    return {r["role_id"]: {"description": r["description"], "hidden": bool(r["hidden"])} for r in rows}


def set_role_description(guild_id, role_id, description):
    conn.execute(
        "INSERT INTO role_info (guild_id, role_id, description) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, role_id) DO UPDATE SET description = excluded.description",
        (guild_id, role_id, description),
    )
    conn.commit()


def set_role_hidden(guild_id, role_id, hidden):
    conn.execute(
        "INSERT INTO role_info (guild_id, role_id, hidden) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id, role_id) DO UPDATE SET hidden = excluded.hidden",
        (guild_id, role_id, int(hidden)),
    )
    conn.commit()


# ---------- Одноразовые миграции ----------
def get_flag(name):
    return conn.execute("SELECT 1 FROM flags WHERE name = ?", (name,)).fetchone() is not None


def set_flag(name):
    conn.execute("INSERT OR IGNORE INTO flags (name) VALUES (?)", (name,))
    conn.commit()


def list_all_memo_buttons():
    return [dict(r) for r in conn.execute("SELECT * FROM memo_buttons").fetchall()]


def list_all_memo_settings():
    return [dict(r) for r in conn.execute("SELECT * FROM memo_settings").fetchall()]


# ---------- Рынок ----------
MARKET_SETTING_FIELDS = {"channel_id", "guarantor_channel_id", "max_active", "days", "rules_text",
                         "guide_channel_id", "guide_message_id", "guarantor_role_id"}
MARKET_LISTING_FIELDS = {"channel_id", "message_id", "status", "closed_reason", "bumped_at"}


def get_market_settings(guild_id):
    row = conn.execute("SELECT * FROM market_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_market_settings(guild_id, **fields):
    assert fields and set(fields) <= MARKET_SETTING_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO market_settings (guild_id, {names}) VALUES (?, {marks}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *fields.values()),
    )
    conn.commit()


def create_market_listing(guild_id, user_id, kind, title, price, description, notes, contact,
                          avatar_url, created_at, expires_at):
    cur = conn.execute(
        "INSERT INTO market_listings (guild_id, user_id, kind, title, price, description, notes, contact, "
        "avatar_url, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, kind, title, price, description, notes, contact, avatar_url, created_at, expires_at),
    )
    conn.commit()
    return cur.lastrowid


def get_market_listing(listing_id):
    row = conn.execute("SELECT * FROM market_listings WHERE id = ?", (listing_id,)).fetchone()
    return dict(row) if row else None


def get_market_listing_by_message(message_id):
    row = conn.execute("SELECT * FROM market_listings WHERE message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def update_market_listing(listing_id, **fields):
    assert fields and set(fields) <= MARKET_LISTING_FIELDS
    assignments = ", ".join(f"{n} = ?" for n in fields)
    conn.execute(f"UPDATE market_listings SET {assignments} WHERE id = ?", (*fields.values(), listing_id))
    conn.commit()


def delete_market_listing(listing_id):
    conn.execute("DELETE FROM market_responses WHERE listing_id = ?", (listing_id,))
    conn.execute("DELETE FROM market_listings WHERE id = ?", (listing_id,))
    conn.commit()


def list_open_market_listings(guild_id, user_id):
    rows = conn.execute(
        "SELECT * FROM market_listings WHERE guild_id = ? AND user_id = ? AND status = 'open' ORDER BY id",
        (guild_id, user_id),
    ).fetchall()
    return [dict(r) for r in rows]


def list_expired_market_listings(now):
    rows = conn.execute(
        "SELECT * FROM market_listings WHERE status = 'open' AND expires_at IS NOT NULL AND expires_at <= ?",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_market_response(listing_id, user_id, message, created_at):
    """Сохраняет отклик. False, если этот человек уже откликался."""
    try:
        conn.execute(
            "INSERT INTO market_responses (listing_id, user_id, message, created_at) VALUES (?, ?, ?, ?)",
            (listing_id, user_id, message, created_at),
        )
    except sqlite3.IntegrityError:
        return False
    conn.commit()
    return True


def list_market_responses(listing_id):
    rows = conn.execute(
        "SELECT * FROM market_responses WHERE listing_id = ? ORDER BY created_at", (listing_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def search_market_listings(guild_id, text, limit=10):
    """Поиск среди открытых объявлений по названию, описанию и примечаниям (без учёта регистра)."""
    wanted = text.strip().casefold()
    rows = conn.execute(
        "SELECT * FROM market_listings WHERE guild_id = ? AND status = 'open' ORDER BY id DESC", (guild_id,)
    ).fetchall()
    found = []
    for r in rows:
        haystack = " ".join(str(r[k] or "") for k in ("title", "description", "notes", "price")).casefold()
        if wanted in haystack:
            found.append(dict(r))
            if len(found) >= limit:
                break
    return found


# ---------- Резервные копии ----------
def backup_to(path):
    """Целостная копия базы (работает и пока бот пишет в неё)."""
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()


# ---------- Временные голосовые комнаты ----------
VOICE_SETTING_FIELDS = {"trigger_channel_id", "category_id", "name_template"}
VOICE_ROOM_FIELDS = {"owner_id", "state", "panel_message_id"}
VOICE_PREF_FIELDS = {"name", "user_limit", "state"}


def get_voice_settings(guild_id):
    row = conn.execute("SELECT * FROM voice_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_voice_settings(guild_id, **fields):
    assert fields and set(fields) <= VOICE_SETTING_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO voice_settings (guild_id, {names}) VALUES (?, {marks}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *fields.values()),
    )
    conn.commit()


def create_voice_room(channel_id, guild_id, owner_id, state, created_at):
    conn.execute(
        "INSERT OR REPLACE INTO voice_rooms (channel_id, guild_id, owner_id, state, created_at) VALUES (?, ?, ?, ?, ?)",
        (channel_id, guild_id, owner_id, state, created_at),
    )
    conn.commit()


def get_voice_room(channel_id):
    row = conn.execute("SELECT * FROM voice_rooms WHERE channel_id = ?", (channel_id,)).fetchone()
    return dict(row) if row else None


def get_voice_room_by_owner(guild_id, owner_id):
    row = conn.execute(
        "SELECT * FROM voice_rooms WHERE guild_id = ? AND owner_id = ?", (guild_id, owner_id)
    ).fetchone()
    return dict(row) if row else None


def list_voice_rooms():
    return [dict(r) for r in conn.execute("SELECT * FROM voice_rooms").fetchall()]


def update_voice_room(channel_id, **fields):
    assert fields and set(fields) <= VOICE_ROOM_FIELDS
    assignments = ", ".join(f"{n} = ?" for n in fields)
    conn.execute(f"UPDATE voice_rooms SET {assignments} WHERE channel_id = ?", (*fields.values(), channel_id))
    conn.commit()


def delete_voice_room(channel_id):
    conn.execute("DELETE FROM voice_rooms WHERE channel_id = ?", (channel_id,))
    conn.commit()


def get_voice_prefs(guild_id, user_id):
    row = conn.execute(
        "SELECT * FROM voice_prefs WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    ).fetchone()
    return dict(row) if row else None


def set_voice_prefs(guild_id, user_id, **fields):
    assert fields and set(fields) <= VOICE_PREF_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO voice_prefs (guild_id, user_id, {names}) VALUES (?, ?, {marks}) "
        f"ON CONFLICT(guild_id, user_id) DO UPDATE SET {updates}",
        (guild_id, user_id, *fields.values()),
    )
    conn.commit()


# ---------- Автомодерация и муты ----------
AUTOMOD_FIELDS = {"enabled", "log_channel_id", "center_channel_id", "center_message_id", "mute_minutes",
                  "delete_messages", "include_mild", "off_categories"}
MOD_ACTION_FIELDS = {"message_id", "status", "until", "minutes"}


def get_automod_settings(guild_id):
    row = conn.execute("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    return dict(row) if row else None


def set_automod_settings(guild_id, **fields):
    assert fields and set(fields) <= AUTOMOD_FIELDS
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{n} = excluded.{n}" for n in fields)
    conn.execute(
        f"INSERT INTO automod_settings (guild_id, {names}) VALUES (?, {marks}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        (guild_id, *fields.values()),
    )
    conn.commit()


def list_automod_centers():
    return [dict(r) for r in conn.execute(
        "SELECT * FROM automod_settings WHERE center_channel_id IS NOT NULL AND center_message_id IS NOT NULL").fetchall()]


def add_automod_word(guild_id, word, kind):
    conn.execute("INSERT OR IGNORE INTO automod_words (guild_id, word, kind) VALUES (?, ?, ?)", (guild_id, word, kind))
    conn.commit()


def remove_automod_word(guild_id, word):
    cur = conn.execute("DELETE FROM automod_words WHERE guild_id = ? AND word = ?", (guild_id, word))
    conn.commit()
    return cur.rowcount > 0


def list_automod_words(guild_id, kind):
    rows = conn.execute("SELECT word FROM automod_words WHERE guild_id = ? AND kind = ? ORDER BY word", (guild_id, kind)).fetchall()
    return [r[0] for r in rows]


def toggle_automod_exempt(guild_id, target_id, kind):
    """Добавляет исключение, а если оно уже есть, убирает. Возвращает True, если теперь исключение есть."""
    row = conn.execute("SELECT 1 FROM automod_exempt WHERE guild_id = ? AND target_id = ? AND kind = ?",
                       (guild_id, target_id, kind)).fetchone()
    if row:
        conn.execute("DELETE FROM automod_exempt WHERE guild_id = ? AND target_id = ? AND kind = ?", (guild_id, target_id, kind))
    else:
        conn.execute("INSERT INTO automod_exempt (guild_id, target_id, kind) VALUES (?, ?, ?)", (guild_id, target_id, kind))
    conn.commit()
    return not row


def list_automod_exempt(guild_id, kind):
    rows = conn.execute("SELECT target_id FROM automod_exempt WHERE guild_id = ? AND kind = ?", (guild_id, kind)).fetchall()
    return [r[0] for r in rows]


def add_mod_action(guild_id, user_id, moderator_id, action, minutes, reason, source, created_at, until,
                   category=None, word=None, excerpt=None, channel_id=None):
    cur = conn.execute(
        "INSERT INTO mod_actions (guild_id, user_id, moderator_id, action, minutes, reason, source, category, word, "
        "excerpt, channel_id, created_at, until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, moderator_id, action, minutes, reason, source, category, word, excerpt, channel_id, created_at, until),
    )
    conn.commit()
    return cur.lastrowid


def get_mod_action(action_id):
    row = conn.execute("SELECT * FROM mod_actions WHERE id = ?", (action_id,)).fetchone()
    return dict(row) if row else None


def get_mod_action_by_message(message_id):
    row = conn.execute("SELECT * FROM mod_actions WHERE message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def update_mod_action(action_id, **fields):
    assert fields and set(fields) <= MOD_ACTION_FIELDS
    assignments = ", ".join(f"{n} = ?" for n in fields)
    conn.execute(f"UPDATE mod_actions SET {assignments} WHERE id = ?", (*fields.values(), action_id))
    conn.commit()


def list_mod_actions(guild_id, user_id, limit=10):
    rows = conn.execute(
        "SELECT * FROM mod_actions WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?", (guild_id, user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def count_mod_actions_since(guild_id, since, action="mute"):
    return conn.execute(
        "SELECT COUNT(*) FROM mod_actions WHERE guild_id = ? AND action = ? AND created_at >= ?", (guild_id, action, since)
    ).fetchone()[0]
