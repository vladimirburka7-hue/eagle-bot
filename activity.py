"""Учёт активности: сообщения и время в голосовых (тексты сообщений не сохраняются)."""
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from utils import (
    ACTIVE_VOICE_SECONDS, can_manage, captain_slot_of, day_key, format_day,
    format_hours, guild_offset, local_now, since_key, squad_member_rows,
    squad_roles, squad_title,
)

NO_PING = discord.AllowedMentions.none()


async def squad_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    choices = []
    for slot, role in squad_roles(interaction.guild):
        title = squad_title(slot)
        if current in title.lower() or current in role.name.lower():
            choices.append(app_commands.Choice(name=f"{title} ({role.name})"[:100], value=slot))
    return choices[:25]


def can_view_squad(guild: discord.Guild, member: discord.Member, slot: int) -> bool:
    if can_manage(member):
        return True
    is_captain, own_slot = captain_slot_of(guild, member)
    return is_captain and own_slot == slot


class Activity(commands.Cog):
    squad_grp = app_commands.Group(name="squad", description="Статистика отряда (капитанам и персоналу)", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions = {}  # (guild_id, user_id) -> время входа в войс

    async def cog_load(self):
        self.flush.start()

    async def cog_unload(self):
        self.flush.cancel()
        for key in list(self.sessions):
            self.credit(key, restart=False)

    # ----- Подсчёт -----
    def credit(self, key, restart=True):
        start = self.sessions.get(key)
        if start is None:
            return
        now = time.time()
        seconds = int(min(max(now - start, 0), 12 * 3600))
        if seconds:
            day = day_key(local_now(guild_offset(key[0])))
            db.add_activity(key[0], key[1], day, voice_seconds=seconds)
        if restart:
            self.sessions[key] = now
        else:
            self.sessions.pop(key, None)

    @tasks.loop(minutes=5)
    async def flush(self):
        """Раз в 5 минут записывает время в войсе, чтобы при сбое ничего не терялось."""
        for key in list(self.sessions):
            self.credit(key)

    @flush.before_loop
    async def before_flush(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                if channel == guild.afk_channel:
                    continue
                for member in channel.members:
                    if not member.bot:
                        self.sessions.setdefault((guild.id, member.id), time.time())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        afk = member.guild.afk_channel
        was = before.channel is not None and before.channel != afk
        now_in = after.channel is not None and after.channel != afk
        key = (member.guild.id, member.id)
        if was and not now_in:
            self.credit(key, restart=False)
        elif now_in and not was:
            self.sessions[key] = time.time()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        day = day_key(local_now(guild_offset(message.guild.id)))
        db.add_activity(message.guild.id, message.author.id, day, messages=1)

    # ----- Команды -----
    @app_commands.command(name="activity", description="Активность участника за 7 и 30 дней")
    @app_commands.describe(user="Участник (по умолчанию вы)")
    @app_commands.guild_only()
    async def activity(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        guild = interaction.guild
        target = user or interaction.user
        if target.id != interaction.user.id and not can_manage(interaction.user):
            allowed = any(
                can_view_squad(guild, interaction.user, slot)
                for slot, role in squad_roles(guild) if role in target.roles
            )
            if not allowed:
                await interaction.response.send_message(
                    "Чужую активность могут смотреть персонал и капитан его отряда.", ephemeral=True
                )
                return
        now = local_now(guild_offset(guild.id))
        week = db.get_activity_totals(guild.id, [target.id], since_key(now, 7)).get(target.id, (0, 0, None))
        month = db.get_activity_totals(guild.id, [target.id], since_key(now, 30)).get(target.id, (0, 0, None))
        text = (
            f"**{target.display_name}**\n"
            f"За 7 дней: {week[0]} сообщ. · {format_hours(week[1])} ч в войсе\n"
            f"За 30 дней: {month[0]} сообщ. · {format_hours(month[1])} ч в войсе\n"
            f"Последняя активность: {format_day(db.get_last_active(guild.id, target.id))}"
        )
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PING)

    @squad_grp.command(name="activity", description="Активность участников отряда")
    @app_commands.describe(squad="Отряд", days="За сколько дней (по умолчанию 7)")
    @app_commands.autocomplete(squad=squad_autocomplete)
    async def squad_activity(
        self,
        interaction: discord.Interaction,
        squad: int,
        days: app_commands.Range[int, 1, 60] = 7,
    ):
        guild = interaction.guild
        role = dict(squad_roles(guild)).get(squad)
        if role is None:
            await interaction.response.send_message("Такого отряда нет.", ephemeral=True)
            return
        if not can_view_squad(guild, interaction.user, squad):
            await interaction.response.send_message(
                "Смотреть отряд могут персонал и его капитан.", ephemeral=True
            )
            return
        since = since_key(local_now(guild_offset(guild.id)), days)
        rows = squad_member_rows(guild, role, since)
        if not rows:
            await interaction.response.send_message("В отряде никого нет.", ephemeral=True)
            return
        rows.sort(key=lambda r: r["messages"] + r["voice"] / 60, reverse=True)
        lines = []
        for r in rows[:40]:
            mark = " " if r["active"] else "!"
            lines.append(
                f"{mark} {r['member'].display_name[:16]:<16} {r['messages']:>4} сообщ  "
                f"{format_hours(r['voice']):>5} ч  {format_day(r['last'])}"
            )
        extra = f"\n…и ещё {len(rows) - 40}" if len(rows) > 40 else ""
        await interaction.response.send_message(
            f"**{squad_title(squad)}: активность за {days} дн.** (! = неактивен)\n"
            f"```\n" + "\n".join(lines) + f"\n```{extra}",
            ephemeral=True,
        )

    @squad_grp.command(name="inactive", description="Неактивные участники отрядов")
    @app_commands.describe(days="Сколько дней без активности (по умолчанию 7)", squad="Отряд (необязательно)")
    @app_commands.autocomplete(squad=squad_autocomplete)
    async def inactive(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 60] = 7,
        squad: Optional[int] = None,
    ):
        guild = interaction.guild
        squads = dict(squad_roles(guild))
        if squad is None:
            is_captain, own = captain_slot_of(guild, interaction.user)
            if can_manage(interaction.user):
                slots = sorted(squads)
            elif is_captain and own in squads:
                slots = [own]
            else:
                await interaction.response.send_message(
                    "Команда для персонала и капитанов.", ephemeral=True
                )
                return
        else:
            if squad not in squads:
                await interaction.response.send_message("Такого отряда нет.", ephemeral=True)
                return
            if not can_view_squad(guild, interaction.user, squad):
                await interaction.response.send_message(
                    "Смотреть отряд могут персонал и его капитан.", ephemeral=True
                )
                return
            slots = [squad]

        since = since_key(local_now(guild_offset(guild.id)), days)
        blocks = []
        for slot in slots:
            quiet = [r for r in squad_member_rows(guild, squads[slot], since) if not r["active"]]
            if quiet:
                names = "\n".join(
                    f"• {r['member'].mention} (был: {format_day(db.get_last_active(guild.id, r['member'].id))})"
                    for r in quiet[:30]
                )
                blocks.append(f"**{squad_title(slot)}** ({len(quiet)}):\n{names}")
        text = "\n\n".join(blocks) or f"Все активны: за {days} дн. неактивных нет."
        await interaction.response.send_message(text[:1990], ephemeral=True, allowed_mentions=NO_PING)

    @app_commands.command(name="top", description="Самые активные участники")
    @app_commands.describe(days="За сколько дней (по умолчанию 7)")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 60] = 7):
        guild = interaction.guild
        since = since_key(local_now(guild_offset(guild.id)), days)
        rows = db.get_top_activity(guild.id, since, 10)
        if not rows:
            await interaction.response.send_message("Пока нет данных об активности.", ephemeral=True)
            return
        lines = [
            f"**{i}.** <@{user_id}> — {messages} сообщ. · {format_hours(voice)} ч в войсе"
            for i, (user_id, messages, voice) in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title=f"Топ активности за {days} дн.", description="\n".join(lines), color=0x5865F2
        )
        await interaction.response.send_message(embed=embed, allowed_mentions=NO_PING)


async def setup(bot: commands.Bot):
    await bot.add_cog(Activity(bot))
