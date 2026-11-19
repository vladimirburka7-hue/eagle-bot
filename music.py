"""Поиск песен: название, исполнитель, альбом, обложка и ссылки. Бот ничего не проигрывает.

Данные берутся из открытого API Deezer (ключ не нужен), запасной вариант: iTunes.
Результат видит только тот, кто искал.
"""
import asyncio
import logging
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from content import build_music_help_payload, build_music_results_payload, respond_components

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
BANNER_FILE = "music_banner.jpg"
ICON_FILE = "music_icon.jpg"
_CACHE = {}


def load_asset(name):
    """Картинка из папки assets (кэшируется). Если файла нет, сообщение просто идёт без неё."""
    if name not in _CACHE:
        try:
            with open(os.path.join(ASSETS, name), "rb") as f:
                _CACHE[name] = f.read()
        except OSError:
            _CACHE[name] = None
    return _CACHE[name]


def parse_deezer(data) -> list:
    tracks = []
    for item in (data or {}).get("data", []) or []:
        album = item.get("album") or {}
        artist = item.get("artist") or {}
        title = item.get("title") or item.get("title_short")
        if not title:
            continue
        tracks.append({
            "title": title,
            "artist": artist.get("name") or "Неизвестный исполнитель",
            "album": album.get("title") or "",
            "duration": item.get("duration") or 0,
            "explicit": bool(item.get("explicit_lyrics")),
            "preview": item.get("preview") or "",
            "link": item.get("link") or "",
            "cover": album.get("cover_medium") or album.get("cover_big") or album.get("cover") or "",
            "year": None, "genre": None,
        })
    return tracks


def parse_itunes(data) -> list:
    tracks = []
    for item in (data or {}).get("results", []) or []:
        title = item.get("trackName")
        if not title:
            continue
        cover = (item.get("artworkUrl100") or "").replace("100x100bb", "300x300bb")
        tracks.append({
            "title": title,
            "artist": item.get("artistName") or "Неизвестный исполнитель",
            "album": item.get("collectionName") or "",
            "duration": int((item.get("trackTimeMillis") or 0) / 1000),
            "explicit": item.get("trackExplicitness") == "explicit",
            "preview": item.get("previewUrl") or "",
            "link": item.get("trackViewUrl") or "",
            "cover": cover,
            "year": (item.get("releaseDate") or "")[:4] or None,
            "genre": item.get("primaryGenreName") or None,
        })
    return tracks


async def _get_json(url: str, params: dict, timeout: float, headers: dict = None):
    proxy = os.getenv("PROXY_URL") or None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as session:
        async with session.get(url, params=params, proxy=proxy) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


async def search_tracks(query: str, limit: int = 5):
    """-> (список песен, источник). Укладывается примерно в 2,5 секунды (ограничение Discord на ответ)."""
    query = " ".join(query.split())[:100]
    try:
        tracks = parse_deezer(await _get_json("https://api.deezer.com/search", {"q": query, "limit": limit}, 1.6))
        if tracks:
            return tracks[:limit], "Deezer"
    except Exception as e:
        logging.warning("Deezer не ответил: %s", e)
    try:
        params = {"term": query, "media": "music", "entity": "song", "limit": limit}
        tracks = parse_itunes(await _get_json("https://itunes.apple.com/search", params, 0.8))
        return tracks[:limit], "iTunes"
    except Exception as e:
        logging.warning("iTunes не ответил: %s", e)
        return None, ""


RADIO_HOSTS = ("de1.api.radio-browser.info", "nl1.api.radio-browser.info", "at1.api.radio-browser.info")
RADIO_HEADERS = {"User-Agent": "EagleDiscordBot/1.0"}


def parse_radio(data) -> list:
    """Ответ Radio Browser -> список станций в том же формате, что и песни (preview = адрес эфира)."""
    stations = []
    for item in data or []:
        url = item.get("url_resolved") or item.get("url") or ""
        if not url.lower().startswith(("http://", "https://")) or item.get("lastcheckok") == 0:
            continue
        favicon = item.get("favicon") or ""
        tags = ", ".join([t for t in (item.get("tags") or "").split(",") if t][:3])
        stations.append({
            "title": (item.get("name") or "Радио").strip(),
            "artist": " · ".join(x for x in (item.get("country"), tags) if x) or "Интернет-радио",
            "album": "", "duration": 0, "explicit": False,
            "preview": url, "link": item.get("homepage") or "",
            "cover": favicon if favicon.lower().startswith("https://") else "",
            "year": None, "genre": None,
        })
    return stations


async def search_radio(query: str, limit: int = 6):
    """-> (список станций, источник) или (None, '') при ошибке. Запрашивает несколько зеркал сразу, берёт первое ответившее."""
    query = " ".join(query.split())[:60]
    params = {"limit": limit * 2, "hidebroken": "true", "order": "clickcount", "reverse": "true"}
    if query:
        params["name"] = query
    else:
        params["countrycode"] = "RU"

    async def one(host):
        return await _get_json(f"https://{host}/json/stations/search", params, 2.2, RADIO_HEADERS)

    tasks = [asyncio.ensure_future(one(h)) for h in RADIO_HOSTS]
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, timeout=2.4, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            for task in done:
                if task.exception() is None:
                    stations = parse_radio(task.result())
                    return stations[:limit], "Radio Browser"
        return None, ""
    finally:
        for task in tasks:
            task.cancel()


async def answer_search(interaction: discord.Interaction, query: str):
    tracks, source = await search_tracks(query)
    if tracks is None:
        await interaction.response.send_message(
            "Сервис музыки сейчас не отвечает. Попробуйте ещё раз через минуту.", ephemeral=True
        )
        return
    icon = load_asset(ICON_FILE)
    payload = build_music_results_payload(query=query, tracks=tracks, source=source or "Deezer",
                                          icon_name=ICON_FILE if icon else None)
    try:
        await respond_components(interaction.client, interaction, payload, (icon, ICON_FILE) if icon else None)
    except discord.HTTPException:
        logging.exception("Не удалось показать результаты поиска песни")
        if not interaction.response.is_done():
            lines = [f"{t['title']} · {t['artist']}" for t in tracks]
            await interaction.response.send_message("\n".join(lines) or "Ничего не нашлось.", ephemeral=True)


class SearchModal(discord.ui.Modal, title="Поиск песни"):
    def __init__(self):
        super().__init__(timeout=300)
        self.query = discord.ui.TextInput(label="Название песни или исполнитель", max_length=100,
                                          placeholder="Например: Кино Группа крови")
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.query.value.strip()
        if not text:
            await interaction.response.send_message("Введите название песни.", ephemeral=True)
            return
        await answer_search(interaction, text)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logging.exception("Ошибка формы поиска песни", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Не удалось выполнить поиск. Попробуйте ещё раз.", ephemeral=True)


class MusicView(discord.ui.View):
    """Кнопка «Найти песню» / «Найти ещё» в инструкции и в результатах."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Найти песню", custom_id="mu:search")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SearchModal())


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(MusicView())

    @app_commands.command(name="music", description="Найти песню: исполнитель, альбом, обложка и ссылки")
    @app_commands.describe(query="Название песни или исполнитель (пусто: показать инструкцию)")
    async def music(self, interaction: discord.Interaction, query: str = ""):
        query = query.strip()
        if query:
            await answer_search(interaction, query)
            return
        banner = load_asset(BANNER_FILE)
        payload = build_music_help_payload(banner_name=BANNER_FILE if banner else None)
        try:
            await respond_components(interaction.client, interaction, payload, (banner, BANNER_FILE) if banner else None)
        except discord.HTTPException:
            logging.exception("Не удалось показать инструкцию по музыке")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Введите /music и название песни: бот пришлёт список с обложками и ссылками.", ephemeral=True
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
