"""Отчёты капитанов: форма /report, автоматическая статистика отряда, напоминания."""
import json
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from emojis import EMOJI
from content import build_report_payload, post_components
from utils import (
    can_manage, captain_slot_of, find_captains, guild_offset, local_now,
    squad_member_rows, squad_roles, squad_summary, squad_title, week_key,
    week_range_text, week_start_key,
)

NO_PING = discord.AllowedMentions.none()
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

QUESTIONS = [
    ("Итоги недели", discord.TextStyle.paragraph, "Что сделал отряд за неделю", True, 900),
    ("Проблемы и нарушения", discord.TextStyle.paragraph, "Если нет, напишите «нет»", True, 500),
    ("Планы на следующую неделю", discord.TextStyle.paragraph, "", True, 600),
    ("Нужна помощь руководства", discord.TextStyle.paragraph, "Необязательно", False, 400),
]


async def squad_autocomplete(interaction: discord.Interaction, current: str):
    current = current.lower()
    return [
        app_commands.Choice(name=f"{squad_title(slot)} ({role.name})"[:100], value=slot)
        for slot, role in squad_roles(interaction.guild)
        if current in squad_title(slot).lower() or current in role.name.lower()
    ][:25]


class ReportModal(discord.ui.Modal, title="Отчёт капитана"):
    def __init__(self, slot: int, role: discord.Role):
        super().__init__(timeout=1800)
        self.slot = slot
        self.role = role
        self.inputs = []
        for label, style, placeholder, required, max_len in QUESTIONS:
            item = discord.ui.TextInput(
                label=label, style=style, placeholder=placeholder or None,
                required=required, max_length=max_len,
            )
            self.add_item(item)
            self.inputs.append(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = db.get_report_settings(guild.id)
        channel = guild.get_channel(settings["channel_id"]) if settings else None
        if channel is None:
            await interaction.followup.send(
                "Канал для отчётов не настроен (команда /setting report setup).", ephemeral=True
            )
            return

        now = local_now(guild_offset(guild.id))
        week = week_key(now)
        rows = squad_member_rows(guild, self.role, week_start_key(now))
        answers = [
            [question[0], item.value.strip() or "—"]
            for question, item in zip(QUESTIONS, self.inputs)
            if item.value.strip() or question[3]
        ]
        payload = build_report_payload(
            squad=squad_title(self.slot),
            week_text=week_range_text(now),
            captain_id=interaction.user.id,
            avatar_url=interaction.user.display_avatar.url,
            stats=squad_summary(rows),
            answers=answers,
        )

        old = db.get_report(guild.id, interaction.user.id, week)
        if old and old["message_id"]:
            old_channel = guild.get_channel(old["channel_id"])
            if old_channel:
                try:
                    await (await old_channel.fetch_message(old["message_id"])).delete()
                except discord.HTTPException:
                    pass
        try:
            message = await post_components(interaction.client, channel, payload)
        except discord.HTTPException as e:
            logging.exception("Не удалось отправить отчёт")
            await interaction.followup.send(f"Не удалось отправить отчёт: {e}", ephemeral=True)
            return
        db.save_report(
            guild.id, interaction.user.id, self.slot, week, int(time.time()),
            channel.id, int(message["id"]),
        )
        await interaction.followup.send(
            f"Отчёт отправлен в {channel.mention}." + (" Предыдущий за эту неделю заменён." if old else ""),
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы отчёта", exc_info=error)
        text = f"Ошибка: {type(error).__name__}. Подробности в окне бота."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class Reports(commands.Cog):
    setting = app_commands.Group(name="report_setting", description="Настройки отчётов капитанов", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.reminders.start()

    async def cog_unload(self):
        self.reminders.cancel()

    # ----- Напоминания -----
    @tasks.loop(minutes=10)
    async def reminders(self):
        for settings in db.list_report_settings():
            try:
                await self.remind(settings)
            except Exception:
                logging.exception("Ошибка напоминания об отчётах")

    @reminders.before_loop
    async def before_reminders(self):
        await self.bot.wait_until_ready()

    async def remind(self, settings: dict):
        guild = self.bot.get_guild(settings["guild_id"])
        if guild is None or settings["weekday"] is None:
            return
        now = local_now(settings["utc_offset"] if settings["utc_offset"] is not None else 3)
        week = week_key(now)
        if now.weekday() != settings["weekday"] or now.hour < (settings["hour"] or 0):
            return
        if settings["last_reminded"] == week:
            return
        db.mark_reminded(guild.id, week)

        channel = guild.get_channel(settings["channel_id"])
        if channel is None:
            return
        submitted = {r["user_id"] for r in db.list_reports_for_week(guild.id, week)}
        missing = [(m, slot) for m, slot in find_captains(guild) if m.id not in submitted]
        if not missing:
            await channel.send(f"Все отчёты за неделю сданы.")
            return
        lines = [
            f"• {m.mention}" + (f" ({squad_title(slot)})" if slot else "") for m, slot in missing
        ]
        await channel.send(
            f"Не сданы отчёты за неделю — /report\n"
            + "\n".join(lines),
            allowed_mentions=discord.AllowedMentions(users=[m for m, _ in missing]),
        )
        for member, _ in missing:
            try:
                await member.send(
                    f"Напоминание с сервера **{guild.name}**: сдайте недельный отчёт отряда "
                    f"командой /report."
                )
            except discord.HTTPException:
                pass

    # ----- Команды -----
    @setting.command(name="setup", description="Настроить канал отчётов и напоминания")
    @app_commands.describe(
        channel="Канал, куда приходят отчёты (его видят шефы)",
        weekday="День напоминания капитанам",
        hour="Час напоминания (0–23)",
        utc_offset="Часовой пояс: сдвиг от UTC (Москва = 3)",
    )
    @app_commands.choices(weekday=[
        app_commands.Choice(name=name, value=i) for i, name in enumerate(WEEKDAYS)
    ])
    async def reports_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        weekday: app_commands.Choice[int],
        hour: app_commands.Range[int, 0, 23] = 18,
        utc_offset: app_commands.Range[int, -12, 14] = 3,
    ):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(
                f"У бота нет права писать в {channel.mention}.", ephemeral=True
            )
            return
        db.set_report_settings(interaction.guild_id, channel.id, weekday.value, hour, utc_offset)
        captains = find_captains(interaction.guild)
        if captains:
            found = "\n".join(
                f"• {m.mention}" + (f" — {squad_title(s)}" if s else " — отряд не определён")
                for m, s in captains
            )
        else:
            found = ("Капитаны не найдены. Роль капитана должна содержать слово "
                     "«captain» или «капитан» и номер отряда (например «The captain of the 2nd group»).")
        await interaction.response.send_message(
            f"Отчёты: {channel.mention}. Напоминание: {weekday.name}, {hour}:00 (UTC{utc_offset:+d}).\n"
            f"**Найденные капитаны:**\n{found}",
            ephemeral=True, allowed_mentions=NO_PING,
        )

    @app_commands.command(name="report", description="Сдать недельный отчёт отряда")
    @app_commands.describe(squad="Отряд (нужно только персоналу)")
    @app_commands.autocomplete(squad=squad_autocomplete)
    @app_commands.guild_only()
    async def squad_report(self, interaction: discord.Interaction, squad: Optional[int] = None):
        guild = interaction.guild
        staff = can_manage(interaction.user)
        is_captain, own_slot = captain_slot_of(guild, interaction.user)
        if not staff and not is_captain:
            await interaction.response.send_message("Команда для капитанов отрядов.", ephemeral=True)
            return
        slot = squad if squad is not None else own_slot
        if slot is None:
            await interaction.response.send_message(
                "Не удалось определить отряд. Укажите его в параметре squad.", ephemeral=True
            )
            return
        if not staff and slot != own_slot:
            await interaction.response.send_message("Сдавать отчёт можно только за свой отряд.", ephemeral=True)
            return
        role = dict(squad_roles(guild)).get(slot)
        if role is None:
            await interaction.response.send_message(f"Роль для отряда {slot} не найдена.", ephemeral=True)
            return
        if not db.get_report_settings(guild.id):
            await interaction.response.send_message(
                "Канал для отчётов ещё не настроен (/setting report setup).", ephemeral=True
            )
            return
        await interaction.response.send_modal(ReportModal(slot, role))

    @setting.command(name="status", description="Кто сдал отчёт на этой неделе")
    async def reports_status(self, interaction: discord.Interaction):
        if not can_manage(interaction.user):
            await interaction.response.send_message(f"Только для персонала.", ephemeral=True)
            return
        guild = interaction.guild
        week = week_key(local_now(guild_offset(guild.id)))
        submitted = {r["user_id"] for r in db.list_reports_for_week(guild.id, week)}
        captains = find_captains(guild)
        if not captains:
            await interaction.response.send_message("Капитаны не найдены.", ephemeral=True)
            return
        lines = [
            f"{EMOJI['ok'] if m.id in submitted else EMOJI['no']} {m.mention}" + (f" ({squad_title(s)})" if s else "")
            for m, s in captains
        ]
        await interaction.response.send_message(
            f"**Отчёты за неделю {week}:**\n" + "\n".join(lines),
            ephemeral=True, allowed_mentions=NO_PING,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Reports(bot))
