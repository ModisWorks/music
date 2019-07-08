"""The music player for the music module"""

import typing
import asyncio
import json
import logging
import os
import random
import threading

import discord
import youtube_dl

from modis import main
from modis.tools import data
from modis.tools import embed

from . import _data, _timebar, api_music, ui_embed

logger = logging.getLogger(__name__)

options_ytdl = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}
options_ffmpeg = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(options_ytdl)


class MusicPlayer:
    """The music player for the music module. This object is tied to a server."""

    def __init__(self,
                 guild_id: int) -> None:
        """Locks onto a guild for easy management of various UIs

        Args:
            guild_id (int): The Discord ID of the guild to lock on to
        """

        self.logger = logging.getLogger("{}.{}".format(__name__, guild_id))

        # Client vars
        self.guild_id = guild_id
        self.voice_channel: typing.Optional[discord.VoiceChannel] = None
        self.voice_client: typing.Optional[discord.VoiceClient] = None
        self.text_channel: typing.Optional[discord.TextChannel] = None
        self.topic_channel: typing.Optional[discord.TextChannel] = None

        # Backend vars
        self.queue: list = []
        self.history: list = []
        self.history_max: int = 500
        self.volume: int = 20
        self.loop: str = "off"  # off/on/shuffle

        # Frontend vars
        self.embed: typing.Optional[embed.UI] = None
        self.queue_display_size: int = 9
        self.ui_fields: list = [
            "nowplaying",
            "author",
            "source",
            "time",
            "queue",
            "queuesize",
            "volume",
            "status"
        ]
        self.ui_loggers: typing.Dict[str, logging.Logger] = {}
        self.topic: str = "Nothing is currently playing."

        # Status vars
        self.ready_text: bool = False
        self.ready_voice: bool = False
        self.state: str = "off"  # off/starting/ready/starting stream

        # Initialise
        for key in self.ui_fields:
            self.ui_loggers[key] = logging.getLogger("{}.{}.{}".format(__name__, self.guild_id, key))
            self.ui_loggers[key].setLevel('DEBUG')
            self.ui_loggers[key].propagate = False

        self.pull_volume()
        self.pull_topic_channel()

    # Commands
    async def play(self,
                   voice_channel: discord.VoiceChannel,
                   text_channel: discord.TextChannel,
                   query: str,
                   index: int = 0,
                   interrupt: bool = False,
                   shuffle: bool = False) -> None:
        """The play command.

        Args:
            voice_channel (discord.VoiceChannel): The member that called the command.
            text_channel (discord.TextChannel): The channel where the command was called.
            query (str): The query that was passed with the command.
            index (int): Whether to play the query next, or at the end of the queue.
            interrupt (bool): Whether to stop the currently playing song.
            shuffle (bool): Whether to shuffle the queue after starting.
        """

        if self.state == "off":
            self.state = "starting"

            self.history = []
            await self.update_topic("The music player is starting")

            await self.text_setup(text_channel)
            await self.voice_setup(voice_channel)

            if self.ready_text and self.ready_text:
                self.state = "ready"
            else:
                self.state = "off"

        if self.state == "ready":
            await self.enqueue(query, index, shuffle)

            if not self.voice_client.is_playing() or interrupt:
                await self.voice_next()
                pass

    # Backend functions
    async def enqueue(self,
                      query: str,
                      index: int = 0,
                      shuffle: bool = False) -> None:
        """Parses a query and adds it to the queue.

        Args:
            query (str): Either a search term or a link.
            index (int): The queue index to enqueue at (0 for end).
            shuffle (bool): Whether to shuffle the added songs.
        """

        self.ui_loggers["status"].info("Parsing \"{}\"".format(query))

        # Parse query
        queue_list = api_music.parse_query(query, self.ui_loggers["status"])
        if not queue_list:
            return

        if shuffle:
            random.shuffle(queue_list)

        if index == 0:
            self.queue.extend(queue_list)
        else:
            self.queue[index-1:index-1] = queue_list

        self.update_queue()

    async def voice_next(self) -> None:
        """Starts playing the next song in the queue."""

        # if self.state != "ready":
        #     logger.error("Attempt to play song from wrong state ('{}'), must be 'ready'.".format(self.state))
        #     return

        self.state = "starting stream"

        if self.voice_client.is_playing():
            self.voice_client.stop()

        # Queue empty
        if not self.queue:
            self.state = "ready"

            if self.loop == "on":
                self.ui_loggers["status"].info("Finished queue; looping")
                self.queue = self.history
            elif self.loop == "shuffle":
                self.ui_loggers["status"].info("Finished queue; looping and shuffling")
                self.queue = self.history
                random.shuffle(self.queue)
            else:
                self.ui_loggers["status"].info("Finished queue")
            self.history = []
            self.update_queue()

            if self.queue:
                await self.voice_next()
            else:
                # TODO stop
                pass
            return

        self.ui_loggers["nowplaying"].debug("---")
        self.ui_loggers["author"].debug("---")
        self.ui_loggers["source"].debug("---")
        self.ui_loggers["status"].debug("Downloading next song")
        self.ui_loggers["time"].debug("Loading song")

        song_link = self.queue[0][0]
        song_name = self.queue[0][1]

        self.history.append(self.queue.pop(0))
        while len(self.history) > self.history_max:
            self.history.pop(0)

        song_filename = ytdl.extract_info(song_link, download=False)
        if "entries" in song_filename:
            song_filename = song_filename["entries"][0]
        song_filename = song_filename["url"]
        source = discord.FFmpegPCMAudio(song_filename, **options_ffmpeg)
        source = discord.PCMVolumeTransformer(source)
        source.volume = self.volume/100

        self.voice_client.play(source, after=lambda e: print(e) if e else lambda: self.voice_after())

        await self.update_topic("Playing {}".format(song_name))

    async def voice_after(self) -> None:
        """Called after a song finishes playing."""

        pass

    async def voice_error(self,
                          error: Exception) -> None:
        """Called if there is an error while playing a song."""

        pass

    # UI functions
    async def text_setup(self,
                         text_channel: discord.TextChannel) -> None:
        """Creates the embed UI on the specified text channel.

        Args:
            text_channel (discord.TextChannel): The text channel to put the UI in.
        """

        if self.ready_text:
            logger.warning("Attempt to init gui when already init")
            return

        # if self.state != "starting":
        #     logger.warning("Attempt to init gui from wrong state ("{}"); must be "starting".".format(self.state))
        #     return

        self.text_channel = text_channel

        # Create gui
        await self.text_channel.trigger_typing()
        self.embed = self.construct_embed()
        await self.embed.send()
        self.ui_loggers["status"].info("Loading buttons")
        for e in ("⏯", "⏮", "⏹", "⏭", "🔀", "🔉", "🔊"):
            try:
                await self.embed.sent_embed.add_reaction(e)
            except discord.ClientException:
                self.ui_loggers["status"].error("I couldn't add the buttons. Check my permissions.")

        self.ready_text = True

    def construct_embed(self) -> embed.UI:
        """Constructs a new embed UI object with default values.

        Returns:
            constructed_embed (embed.UI): The new embed UI object.
        """

        # Create initial gui values
        queue_display = []
        for i in range(self.queue_display_size):
            queue_display.append("{}. ---\n".format(str(i + 1)))
        datapacks = [
            ("Now playing", "---", False),
            ("Author", "---", True),
            ("Source", "---", True),
            ("Time", "```http\n" + _timebar.make_timebar() + "\n```", False),
            ("Queue", "```md\n{}\n```".format(''.join(queue_display)), False),
            ("Songs left in queue", "---", True),
            ("Volume", "{}%".format(self.volume), True),
            ("Status", "```---```", False)
        ]

        # Create embed UI object
        constructed_embed = embed.UI(
            self.text_channel,
            "",
            "",
            modulename="music",
            colour=_data.MODULECOLOUR,
            datapacks=datapacks
        )

        # Add logging handlers for gui updates
        formatter_none = logging.Formatter("{message}", style="{")
        formatter_time = logging.Formatter("```http\n{message}\n```", style="{")
        formatter_md = logging.Formatter("```md\n{message}\n```", style="{")
        formatter_volume = logging.Formatter("{message}%", style="{")
        formatter_status = logging.Formatter("```__{levelname}__\n{message}\n```", style="{")
        for i in range(len(self.ui_fields)):
            field = self.ui_fields[i]
            handler = EmbedLogHandler(constructed_embed, i)

            if field in ["nowplaying", "author", "source", "queue_size"]:
                handler.setFormatter(formatter_none)
            elif field == "time":
                handler.setFormatter(formatter_time)
            elif field == "queue":
                handler.setFormatter(formatter_md)
            elif field == "volume":
                handler.setFormatter(formatter_volume)
            elif field == "status":
                handler.setFormatter(formatter_status)

            self.ui_loggers[field].addHandler(handler)

        return constructed_embed

    def update_queue(self) -> None:
        """Updates the queue display in the UI."""

        queue_display = []
        for i in range(self.queue_display_size):
            try:
                if len(self.queue[i][1]) > 40:
                    songname = self.queue[i][1][:37] + "..."
                else:
                    songname = self.queue[i][1]
            except IndexError:
                songname = "---"
            queue_display.append("{}. {}\n".format(str(i + 1), songname))

        self.ui_loggers["queue"].debug(''.join(queue_display))
        self.ui_loggers["queuesize"].debug(str(len(self.queue)))

    async def update_topic(self,
                           new_topic: str = "") -> None:
        """Updates the channel topic if a topic channel is configured.

        Args:
            new_topic (str): The string to update the channel topic to.
        """

        if new_topic:
            self.topic = new_topic

        if self.topic_channel:
            await self.topic_channel.edit(topic=self.topic)

    # Voice functions
    async def voice_setup(self,
                          voice_channel: discord.VoiceChannel) -> None:
        """Connects to the specified voice channel

        Args:
            voice_channel (discord.VoiceChannel): The voice channel to connect to.
        """

        if self.ready_voice:
            logger.warning("Attempt to init voice when already init")
            return

        # if self.state != "starting":
        #     logger.warning("Attempt to init voice from wrong state ("{}"); must be "starting".".format(self.state))
        #     return

        self.voice_channel = voice_channel

        # Connect to voice
        if self.voice_channel:
            self.ui_loggers["status"].info("Connecting to voice")
            try:
                self.voice_client = await self.voice_channel.connect()
            except discord.ClientException:
                self.ui_loggers["status"].warning("I'm already connected to voice, or don't have permission to.")
                return
            except discord.opus.OpusNotLoaded as e:
                logger.exception(e)
                logger.error("Could not load Opus. There's an error with your FFmpeg setup.")
                self.ui_loggers["status"].error("Could not load Opus. Contact the bot admin.")
                return
        else:
            self.ui_loggers["status"].error("You're not connected to a voice channel.")
            return

    # Database functions
    def push_volume(self) -> None:
        """Pushes the volume setting to the database."""

        data.edit(self.guild_id, "music", self.volume, ["volume"])

    def pull_volume(self) -> None:
        """Pulls the volume setting from the database. If the volume parameter doesn't exist, creates it."""

        if "volume" in data.get(self.guild_id, "music"):
            self.volume = data.get(self.guild_id, "music", ["volume"])
        else:
            self.push_volume()

    def push_topic_channel(self) -> None:
        """Pushes the ID of the chosen channel for topic status updates to the database."""

        data.edit(self.guild_id, "music", self.topic_channel.id, ["topic_channel_id"])

    def pull_topic_channel(self) -> None:
        """Pulls the ID of the chosen channel for topic status updates from the database. If the topic_channel_id paramater doesn't exist, creates it."""

        if "topic_channel_id" in data.get(self.guild_id, "music"):
            self.topic_channel = main.client.get_channel(data.get(self.guild_id, "music", ["topic_channel_id"]))
        else:
            self.push_topic_channel()


class AudioSource(discord.PCMVolumeTransformer):
    """An audio source for the player that plays links through ytdl"""

    def __init__(self, source, *, data, volume):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url, loop: asyncio.AbstractEventLoop):
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"]
        return cls(discord.FFmpegPCMAudio(filename, **options_ffmpeg), data=data)


class EmbedLogHandler(logging.Handler):
    """A custom logging handler that also updates an embed UI."""

    def __init__(self,
                 target_embed: embed.UI,
                 line: int):
        """Initialise the logging handler with extra variables.

        Args:
            target_embed (ui_embed.UI): The embed UI to update with the log.
            line (int): The embed field to update.
        """

        logging.Handler.__init__(self)

        self.embed = target_embed
        self.line = line

    def flush(self):
        try:
            asyncio.run_coroutine_threadsafe(self.usend_when_ready(), main.client.loop)
        except Exception as e:
            logger.exception(e)
            return

    async def usend_when_ready(self):
        if self.embed is not None:
            await self.embed.usend()

    def emit(self, record):
        msg = self.format(record)
        msg = msg.replace("__DEBUG__", "")\
            .replace("__INFO__", "")\
            .replace("__WARNING__", "css")\
            .replace("__ERROR__", "http")\
            .replace("__CRITICAL__", "http")

        try:
            self.embed.update_data(self.line, msg)
        except AttributeError:
            return
        self.flush()
