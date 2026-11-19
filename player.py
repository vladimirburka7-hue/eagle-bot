"""Музыкальный плеер в голосовой комнате: очередь, пауза, следующий, повтор, громкость.

Что воспроизводится: 30-секундные фрагменты песен из каталога (Deezer / iTunes), прямые ссылки
на аудиофайлы и интернет-радио. Полные песни с YouTube, Spotify и подобных сервисов бот не
проигрывает: это нарушает правила этих сервисов.

Нужно один раз установить PyNaCl и davey (install_voice.bat) и положить ffmpeg.exe в папку бота.
"""
import asyncio
import ipaddress
import logging
import os
import random
import shutil
import time
from importlib.util import find_spec
from urllib.parse import unquote, urlparse

import discord
from discord.ext import commands

from emojis import EMOJI
from content import build_music_results_payload, build_player_payload, edit_components, post_components, respond_components
from music import ICON_FILE, load_asset, search_radio, search_tracks

PLAYER_IMAGE = "functions.jpg"
MAX_QUEUE = 50
IDLE_SECONDS = 120
PICK_TTL = 600
PICK_CACHE = {}  # user_id -> (время, список песен)
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
BLOCKED_HOSTS = ("youtube.com", "youtu.be", "spotify.com", "music.yandex", "vk.com", "soundcloud.com", "deezer.com",
                 "music.apple.com", "tiktok.com")
HERE = os.path.dirname(os.path.abspath(__file__))
FFMPEG_LOG = os.path.join(HERE, "ffmpeg_last.log")  # сюда ffmpeg пишет причину, если не смог открыть источник
MIN_PLAY_SECONDS = 2.0  # если «трек» закончился раньше, считаем, что воспроизвести не удалось


# ---------- Проверки и вспомогательное ----------
def voice_problems() -> list:
    """Каких библиотек для голоса не хватает."""
    return [name for name, module in (("PyNaCl", "nacl"), ("davey", "davey")) if find_spec(module) is None]


def find_ffmpeg():
    """ffmpeg: FFMPEG_PATH из .env, папка бота, папка ffmpeg\\bin или системный PATH."""
    candidates = [os.getenv("FFMPEG_PATH"), os.path.join(HERE, "ffmpeg.exe"), os.path.join(HERE, "ffmpeg"),
                  os.path.join(HERE, "ffmpeg", "bin", "ffmpeg.exe")]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return shutil.which("ffmpeg")


def valid_audio_url(url: str):
    """-> (можно ли, причина). Блокирует сервисы, правила которых запрещают такое, и внутренние адреса."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host:
        return False, "Нужна ссылка вида https://…"
    if any(host == h or host.endswith("." + h) or host.startswith(h) for h in BLOCKED_HOSTS):
        return False, "Ссылки на YouTube, Spotify и подобные сервисы не поддерживаются: их правила запрещают такое воспроизведение."
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return False, "Эта ссылка не подходит."
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False, "Эта ссылка не подходит."
    except ValueError:
        pass
    return True, ""


def title_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(os.path.basename(parsed.path))
    if name:
        name = os.path.splitext(name)[0]
    return (name or parsed.hostname or "Аудио")[:80]


class Queue:
    """Очередь песен без привязки к Discord (удобно проверять)."""

    def __init__(self):
        self.tracks, self.index, self.loop = [], 0, "off"

    def add(self, track) -> bool:
        if len(self.tracks) >= MAX_QUEUE:
            return False
        self.tracks.append(track)
        return True

    def current(self):
        return self.tracks[self.index] if 0 <= self.index < len(self.tracks) else None

    def advance(self):
        """Трек закончился сам: что играть дальше (None, если очередь кончилась)."""
        if not self.tracks:
            return None
        if self.loop == "one" and self.current():
            return self.current()
        if self.index + 1 < len(self.tracks):
            self.index += 1
            return self.current()
        if self.loop == "all":
            self.index = 0
            return self.current()
        self.index = len(self.tracks)
        return None

    def skip(self):
        """Кнопка «Вперёд»."""
        if not self.tracks:
            return None
        if self.index + 1 < len(self.tracks):
            self.index += 1
        elif self.loop == "all":
            self.index = 0
        else:
            self.index = len(self.tracks)
            return None
        return self.current()

    def back(self):
        if not self.tracks:
            return None
        self.index = max(0, min(self.index, len(self.tracks)) - 1)
        return self.current()

    def shuffle(self):
        rest = self.tracks[self.index + 1:]
        random.shuffle(rest)
        self.tracks[self.index + 1:] = rest

    def cycle_loop(self) -> str:
        order = ("off", "all", "one")
        self.loop = order[(order.index(self.loop) + 1) % 3]
        return self.loop

    def upcoming(self):
        return self.tracks[self.index + 1:]

    def drop_current(self):
        """Убирает неработающий трек; индекс теперь указывает на следующий (или за конец)."""
        if 0 <= self.index < len(self.tracks):
            self.tracks.pop(self.index)
        return self.current()

    def clear(self):
        self.tracks, self.index = [], 0


class GuildPlayer:
    def __init__(self):
        self.queue = Queue()
        self.volume = 0.5
        self.ignore_end = 0
        self.text_channel_id = None
        self.panel_message_id = None
        self.idle_task = None
        self.started = 0.0
        self.fail_streak = 0


# ---------- Формы и выбор ----------
class AddMusicModal(discord.ui.Modal, title="Добавить музыку"):
    def __init__(self):
        super().__init__(timeout=300)
        self.query = discord.ui.TextInput(
            label="Песня, «радио …» или ссылка на аудио", max_length=300,
            placeholder="Кино Группа крови, радио рок или https://…/song.mp3",
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Player")
        member = interaction.user
        if not (member.voice and member.voice.channel):
            await interaction.response.send_message("Сначала зайдите в голосовую комнату, затем добавьте музыку.", ephemeral=True)
            return
        text = self.query.value.strip()
        if text.lower().startswith(("http://", "https://")):
            ok, reason = valid_audio_url(text)
            if not ok:
                await interaction.response.send_message(reason, ephemeral=True)
                return
            await interaction.response.send_message("Добавляю в очередь…", ephemeral=True)
            await cog.add_track(interaction, {"title": title_from_url(text), "artist": "Прямая ссылка", "url": text, "cover": None})
            return
        lower = text.lower()
        radio = lower in ("радио", "radio") or lower.startswith(("радио ", "radio "))
        if radio:
            tracks, source = await search_radio(text.split(" ", 1)[1] if " " in text else "")
        else:
            tracks, source = await search_tracks(text)
        if tracks is None:
            await interaction.response.send_message(
                ("Каталог радио" if radio else "Каталог песен") + " сейчас не отвечает. Попробуйте ещё раз через минуту.", ephemeral=True)
            return
        PICK_CACHE[member.id] = (time.time(), tracks)
        icon = load_asset(ICON_FILE)
        payload = build_music_results_payload(query=text, tracks=tracks, source=source, icon_name=ICON_FILE if icon else None, pick=True, radio=radio)
        try:
            await respond_components(interaction.client, interaction, payload, (icon, ICON_FILE) if icon else None)
        except discord.HTTPException:
            logging.exception("Не удалось показать список песен для выбора")
            if not interaction.response.is_done():
                await interaction.response.send_message("Не удалось показать список. Попробуйте ещё раз.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы добавления музыки", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Не удалось добавить музыку. Попробуйте ещё раз.", ephemeral=True)


class PickSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="mu:pick", placeholder="Выберите песню",
                         options=[discord.SelectOption(label="песня", value="0")])

    async def callback(self, interaction: discord.Interaction):
        entry = PICK_CACHE.get(interaction.user.id)
        if not entry or time.time() - entry[0] > PICK_TTL:
            await interaction.response.send_message("Список устарел. Найдите песню снова.", ephemeral=True)
            return
        try:
            track = entry[1][int(self.values[0])]
        except (ValueError, IndexError):
            await interaction.response.send_message("Не удалось определить песню.", ephemeral=True)
            return
        if not track.get("preview") or not valid_audio_url(track["preview"])[0]:
            await interaction.response.send_message("Для этой записи нет подходящего адреса для воспроизведения.", ephemeral=True)
            return
        await interaction.response.send_message("Добавляю в очередь…", ephemeral=True)
        await interaction.client.get_cog("Player").add_track(
            interaction, {"title": track["title"], "artist": track["artist"], "url": track["preview"], "cover": track.get("cover")}
        )


class PickView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PickSelect())


# ---------- Кнопки плеера ----------
async def control_guard(interaction: discord.Interaction):
    cog = interaction.client.get_cog("Player")
    gp = cog.players.get(interaction.guild_id)
    vc = interaction.guild.voice_client
    if gp is None or vc is None:
        await interaction.response.send_message("Плеер сейчас не запущен.", ephemeral=True)
        return None
    member = interaction.user
    if not (member.voice and member.voice.channel and member.voice.channel.id == vc.channel.id):
        await interaction.response.send_message("Зайдите в голосовую комнату, где играет бот.", ephemeral=True)
        return None
    return cog, gp, vc


class PlayerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Назад", custom_id="pl:prev", row=0)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            gp.queue.back()
            cog.stop_quiet(gp, vc)
            await cog.play_current(interaction.guild)
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Пауза / играть", custom_id="pl:toggle", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            if vc.is_paused():
                vc.resume()
            elif vc.is_playing():
                vc.pause()
            elif gp.queue.current():
                await cog.play_current(interaction.guild)
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Вперёд", custom_id="pl:next", row=0)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            track = gp.queue.skip()
            cog.stop_quiet(gp, vc)
            if track:
                await cog.play_current(interaction.guild)
            else:
                await cog.schedule_idle(interaction.guild)
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Стоп", custom_id="pl:stop", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            gp.queue.clear()
            cog.stop_quiet(gp, vc)
            await vc.disconnect()
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Очередь", custom_id="pl:queue", row=0)
    async def queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Player")
        gp = cog.players.get(interaction.guild_id)
        if not gp or not gp.queue.tracks:
            await interaction.response.send_message("Очередь пуста.", ephemeral=True)
            return
        lines = [f"{EMOJI['whitebutterfly'] + ' ' if i == gp.queue.index else ''}{i + 1}. {t['title']} · {t['artist']}" for i, t in enumerate(gp.queue.tracks[:30])]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @discord.ui.button(label="Перемешать", custom_id="pl:shuffle", row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            gp.queue.shuffle()
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Повтор", custom_id="pl:loop", row=1)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            gp.queue.cycle_loop()
            await cog.refresh_panel(interaction.guild)

    async def _volume(self, interaction: discord.Interaction, delta: float):
        ctx = await control_guard(interaction)
        if ctx:
            cog, gp, vc = ctx
            await interaction.response.defer()
            gp.volume = round(min(1.0, max(0.1, gp.volume + delta)), 2)
            if isinstance(vc.source, discord.PCMVolumeTransformer):
                vc.source.volume = gp.volume
            await cog.refresh_panel(interaction.guild)

    @discord.ui.button(label="Тише", custom_id="pl:vdown", row=1)
    async def vdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._volume(interaction, -0.1)

    @discord.ui.button(label="Громче", custom_id="pl:vup", row=1)
    async def vup(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._volume(interaction, 0.1)


# ---------- Модуль ----------
class Player(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players = {}

    async def cog_load(self):
        self.bot.add_view(PlayerView())
        self.bot.add_view(PickView())

    # ----- Воспроизведение -----
    def stop_quiet(self, gp: GuildPlayer, vc):
        """Останавливает трек так, чтобы окончание не засчиталось как «трек закончился сам»."""
        if vc.is_playing() or vc.is_paused():
            gp.ignore_end += 1
            vc.stop()

    async def play_current(self, guild: discord.Guild, depth: int = 0):
        gp, vc = self.players.get(guild.id), guild.voice_client
        track = gp.queue.current() if gp else None
        if gp is None or vc is None or track is None:
            return
        try:
            try:
                log = open(FFMPEG_LOG, "wb")
            except OSError:
                log = None
            source = discord.FFmpegPCMAudio(track["url"], executable=find_ffmpeg(), stderr=log,
                                            before_options="-loglevel warning " + FFMPEG_BEFORE, options="-vn")
            source = discord.PCMVolumeTransformer(source, volume=gp.volume)
            gp.started = time.time()
            vc.play(source, after=self._make_after(guild.id))
            if gp.idle_task:
                gp.idle_task.cancel()
        except Exception:
            logging.exception("Не удалось воспроизвести %s", track.get("title"))
            if depth < 5 and gp.queue.advance():
                await self.play_current(guild, depth + 1)

    def _make_after(self, guild_id: int):
        def after(error):
            asyncio.run_coroutine_threadsafe(self.on_end(guild_id, error), self.bot.loop)
        return after

    async def on_end(self, guild_id: int, error):
        gp, guild = self.players.get(guild_id), self.bot.get_guild(guild_id)
        if gp is None or guild is None:
            return
        if error:
            logging.warning("Ошибка воспроизведения: %s", error)
        if gp.ignore_end > 0:
            gp.ignore_end -= 1
            return
        if time.time() - gp.started < MIN_PLAY_SECONDS:  # закончилось сразу: источник не открылся, не зацикливаемся
            gp.fail_streak += 1
            bad = gp.queue.current()
            await self.notify_failure(guild, gp, bad)
            nxt = gp.queue.drop_current() if gp.fail_streak < 3 else None
            if nxt:
                await self.play_current(guild)
            else:
                if gp.fail_streak >= 3:
                    gp.queue.clear()
                await self.schedule_idle(guild)
            await self.refresh_panel(guild)
            return
        gp.fail_streak = 0
        if gp.queue.advance():
            await self.play_current(guild)
        else:
            await self.schedule_idle(guild)
        await self.refresh_panel(guild)

    async def notify_failure(self, guild: discord.Guild, gp: GuildPlayer, track):
        channel = guild.get_channel(gp.text_channel_id) if gp.text_channel_id else None
        if channel is None or track is None:
            return
        try:
            await channel.send(
                f"Не удалось воспроизвести «{track.get('title', 'трек')}»: ffmpeg не смог открыть источник "
                f"(проверьте интернет и ссылку). Подробности в файле ffmpeg_last.log в папке бота.",
                delete_after=90,
            )
        except discord.HTTPException:
            pass

    async def schedule_idle(self, guild: discord.Guild):
        gp = self.players.get(guild.id)
        if gp is None:
            return
        if gp.idle_task:
            gp.idle_task.cancel()

        async def leave_later():
            await asyncio.sleep(IDLE_SECONDS)
            vc = guild.voice_client
            if vc and not (vc.is_playing() or vc.is_paused()):
                await vc.disconnect()
                gp.queue.clear()
                await self.refresh_panel(guild)

        gp.idle_task = self.bot.loop.create_task(leave_later())

    async def refresh_panel(self, guild: discord.Guild):
        gp, vc = self.players.get(guild.id), guild.voice_client
        if gp is None or gp.text_channel_id is None:
            return
        payload = build_player_payload(
            track=gp.queue.current(), paused=bool(vc and vc.is_paused()), loop=gp.queue.loop, volume=gp.volume,
            upcoming=gp.queue.upcoming(), total=len(gp.queue.upcoming()),
            image_name=PLAYER_IMAGE if load_asset(PLAYER_IMAGE) else None,
        )
        if gp.panel_message_id:
            try:
                await edit_components(self.bot, gp.text_channel_id, gp.panel_message_id, payload)
                return
            except discord.HTTPException:
                gp.panel_message_id = None
        channel = guild.get_channel(gp.text_channel_id)
        image = load_asset(PLAYER_IMAGE)
        if channel is None:
            return
        try:
            message = await post_components(self.bot, channel, payload, (image, PLAYER_IMAGE) if image else None)
            gp.panel_message_id = int(message["id"])
        except discord.HTTPException:
            logging.warning("Не удалось отправить плеер в чат комнаты")

    async def add_track(self, interaction: discord.Interaction, track: dict):
        """Добавляет трек в очередь и запускает воспроизведение. Ответ идёт сообщением «только для вас»."""
        guild, member = interaction.guild, interaction.user
        say = lambda text: interaction.followup.send(text, ephemeral=True)
        missing = voice_problems()
        if missing:
            await say("Для музыки нужно один раз установить " + " и ".join(missing) + ": запустите install_voice.bat и перезапустите бота.")
            return
        if not find_ffmpeg():
            await say("Для музыки нужен ffmpeg: положите ffmpeg.exe в папку бота (см. УСТАНОВКА.txt) и перезапустите бота.")
            return
        channel = member.voice.channel if member.voice else None
        if channel is None:
            await say("Зайдите в голосовую комнату, затем добавьте музыку.")
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):  # без права «Говорить» бот в комнате немой
            try:
                await channel.set_permissions(guild.me, connect=True, speak=True, reason="Бот должен говорить в комнате")
            except discord.HTTPException:
                pass
            perms = channel.permissions_for(guild.me)
            if not (perms.connect and perms.speak):
                await say("У бота нет права «Говорить» в этом канале. Выдайте роли бота «Подключаться» и «Говорить» "
                          "(в настройках роли или категории комнат) и добавьте музыку снова.")
                return
        vc = guild.voice_client
        if vc and vc.channel.id != channel.id and any(not m.bot for m in vc.channel.members):
            await say("Бот сейчас играет в другом голосовом канале.")
            return
        gp = self.players.setdefault(guild.id, GuildPlayer())
        if not gp.queue.add(track):
            await say("Очередь заполнена.")
            return
        try:
            if vc is None:
                vc = await channel.connect(timeout=20, reconnect=True, self_deaf=True)
            elif vc.channel.id != channel.id:
                await vc.move_to(channel)
        except Exception as e:
            logging.exception("Не удалось подключиться к голосовому каналу")
            gp.queue.tracks.pop()
            await say(f"Не удалось подключиться к голосовому каналу ({type(e).__name__}). Проверьте права бота «Подключаться» и «Говорить».")
            return
        if gp.text_channel_id != channel.id:
            gp.text_channel_id, gp.panel_message_id = channel.id, None
        if not (vc.is_playing() or vc.is_paused()):
            if gp.queue.current() is None:
                gp.queue.index = len(gp.queue.tracks) - 1
            await self.play_current(guild)
        await self.refresh_panel(guild)
        await say(f"Добавлено в очередь: {track['title']}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Если в канале с ботом не осталось людей, бот выходит и очищает очередь."""
        vc = member.guild.voice_client
        gp = self.players.get(member.guild.id)
        if vc and gp and before.channel and before.channel.id == vc.channel.id:
            if not any(not m.bot for m in vc.channel.members):
                gp.queue.clear()
                self.stop_quiet(gp, vc)
                await vc.disconnect()
                await self.refresh_panel(member.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Player(bot))
