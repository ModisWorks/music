"""The music player for the music module"""

import typing
import asyncio
import logging
import random

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
    'source_address': '0.0.0.0',  # bind to ipv4 since ipv6 addresses cause issues sometimes
}
options_ffmpeg = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
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
        self.volume: int = 10
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
        self.ready_player: bool = False
        self.busy_player: bool = False
        self.busy_streamer: bool = False

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
        """Starts playback, or queues query if already playing.

        Args:
            voice_channel (discord.VoiceChannel): The member that called the command.
            text_channel (discord.TextChannel): The channel where the command was called.
            query (str): The query that was passed with the command.
            index (int): Whether to play the query next, or at the end of the queue.
            interrupt (bool): Whether to stop the currently playing song.
            shuffle (bool): Whether to shuffle the queue after starting.
        """

        if not self.ready_player:
            self.busy_player = True

            self.history = []
            await self.update_topic("The music player is starting")

            await self.text_setup(text_channel)
            await self.voice_setup(voice_channel)

            if self.ready_text and self.ready_text:
                self.busy_player = False
                self.ready_player = True
            else:
                self.busy_player = False

        if self.ready_player:
            await self.enqueue(query, index, shuffle)

            if not self.voice_client.is_playing() or interrupt:
                await self.voice_next()
                pass

    async def stop(self):
        """Stops playback."""

        self.ready_player = False
        self.busy_player = True

        await self.update_topic("Music player is stopped")
        self.ui_loggers["nowplaying"].debug("---")
        self.ui_loggers["author"].debug("---")
        self.ui_loggers["source"].debug("---")
        self.ui_loggers["status"].debug("Stopping")

        self.ready_voice = False
        self.loop = False

        if self.voice_client:
            try:
                await self.voice_client.disconnect()
            except discord.ClientException:
                pass

        self.voice_client = None
        self.voice_channel = None
        self.queue = []
        self.history = []

        self.update_queue()

        self.ui_loggers["status"].debug("Stopped")

        self.busy_player = False

    async def destroy(self):
        """Destroys the GUI and music player."""

        self.ready_player = False
        self.busy_player = True

        await self.update_topic("Music player is off")

        self.ready_text = False
        self.ready_voice = False
        self.loop = "off"

        if self.voice_client:
            try:
                await self.voice_client.disconnect()
            except discord.ClientException:
                pass

        self.voice_client = None
        self.voice_channel = None
        self.queue = []
        self.history = []

        if self.embed:
            await self.embed.delete()
            self.embed = None

        self.busy_player = False

    async def insert(self):
        pass

    async def pause(self) -> None:
        """Pauses playback if playing."""

        if not self.ready_player:
            return
        if not self.voice_client:
            return
        if not self.voice_client.is_connected():
            return
        if not self.voice_client.is_playing():
            return

        self.voice_client.pause()
        self.ui_loggers["status"].info("Paused")

    async def resume(self) -> None:
        """Resumes playback if paused."""

        if not self.ready_player:
            return
        if not self.voice_client.is_connected():
            return
        if self.voice_client.is_playing():
            return

        self.voice_client.resume()
        self.ui_loggers["status"].info("Resumed")

    async def toggle(self) -> None:
        """Toggles between pause and resume."""

        if not self.ready_player:
            return
        if not self.voice_client.is_connected():
            return

        if self.voice_client.is_playing():
            await self.pause()
        else:
            await self.resume()

    async def skip(self,
                   amount: str = "1") -> None:
        """Skips a specified number of songs.

        Args:
            amount (str): The number of items to skip, can be a number or "all".
        """

        if not self.ready_player:
            return

        if amount == "":
            amount = "1"
        elif amount == "all":
            amount = str(len(self.queue) + 1)

        try:
            num = int(amount)
        except TypeError or ValueError:
            self.ui_loggers["status"].error("Skip amount must be a positive integer or \"all\"")
            return
        # TODO move to on_command.py

        self.ui_loggers["status"].info("Skipping")

        for i in range(num - 1):
            if len(self.queue) > 0:
                self.history.append(self.queue.pop(0))
        self.update_queue()

        try:
            self.voice_client.stop()
        except discord.ClientException or AttributeError:
            pass

    async def remove(self,
                     index: str = "0") -> None:
        """Removes a song or songs from the queue.

        Args:
            index (int): The index to remove, can be either a number, a range in the form '##-##', or "all".
        """

        if not self.ready_player:
            return

        if not index:
            self.ui_loggers["status"].error("You need to provide an index or range to remove")
            return

        if index == "all":
            self.queue = []
            self.update_queue()
            self.ui_loggers["status"].info("Removed all songs")
            return

        indexes = index.split("-")
        try:
            if len(indexes) == 1:
                num_lower = int(indexes[0]) - 1
                num_upper = num_lower + 1
            elif len(indexes) == 2:
                num_lower = int(indexes[0]) - 1
                num_upper = int(indexes[1])
            else:
                self.ui_loggers["status"].error("Cannot have more than 2 indexes for remove range")
                return
        except TypeError or ValueError:
            self.ui_loggers["status"].error("Remove indexes must be positive integers or \"all\"")
            return

        if num_lower < 0 or num_lower >= len(self.queue) or num_upper > len(self.queue):
            if len(self.queue) == 0:
                self.ui_loggers["status"].warning("No songs in queue")
            elif len(self.queue) == 1:
                self.ui_loggers["status"].error("Remove index must be 1 (only 1 song in queue)")
            else:
                self.ui_loggers["status"].error("Remove index must be between 1 and {}".format(len(self.queue)))
            return

        if num_upper <= num_lower:
            self.ui_loggers["status"].error("Second index in range must be greater than first")
            return

        lower_songname = self.queue[num_lower][1]
        for num in range(0, num_upper - num_lower):
            self.logger.debug("Removed {}".format(self.queue[num_lower][1]))
            self.queue.pop(num_lower)

        if len(indexes) == 1:
            self.ui_loggers["status"].info("Removed {}".format(lower_songname))
        else:
            self.ui_loggers["status"].info("Removed songs {}-{}".format(num_lower + 1, num_upper))

        self.update_queue()

    async def rewind(self,
                     amount: str = "1") -> None:
        """Rewinds a specified number of songs

        Args:
            amount (str): The number of items to rewind
        """

        if not self.ready_player:
            return

        if amount == "":
            amount = "1"

        try:
            num = int(amount)
        except TypeError or ValueError:
            self.ui_loggers["status"].error("Rewind argument must be a positive integer")
            return

        if len(self.history) == 0:
            self.ui_loggers["status"].error("No songs to rewind")
            return

        if num < 0:
            self.ui_loggers["status"].error("Rewind must be postitive or 0")
            return
        elif num > len(self.history):
            self.ui_loggers["status"].warning("Rewinding to start")
        else:
            self.ui_loggers["status"].info("Rewinding")

        for i in range(num + 1):
            if len(self.history) > 0:
                self.queue.insert(0, self.history.pop())

        try:
            self.voice_client.stop()
        except discord.ClientException or AttributeError:
            pass

    async def shuffle(self) -> None:
        """Shuffles the queue."""

        if not self.ready_player:
            return

        self.ui_loggers["status"].debug("Shuffling")

        random.shuffle(self.queue)
        self.update_queue()
        self.ui_loggers["status"].debug("Shuffled")

    async def loop(self,
                   loop_type: str = "on") -> None:
        """Changes the loop behaviour.

        Args:
            loop_type (str): The type of loop behaviour, can be "off", "on", or "shuffle".
        """

        if loop_type not in ["on", "off", "shuffle"]:
            self.ui_loggers["status"].error("Loop value must be `off`, `on`, or `shuffle`")
            return

        self.loop = loop_type
        if self.loop == 'on':
            self.ui_loggers["status"].info("Looping on")
        elif self.loop == 'off':
            self.ui_loggers["status"].info("Looping off")
        elif self.loop == 'shuffle':
            self.ui_loggers["status"].info("Looping on and shuffling")

    async def volume(self,
                     value: str = "10") -> None:
        """Changes the volume of the music player.

        Args:
            value (str): The volume to change to, can be an integer from 0 to 100, or + or -.
        """

        if not self.ready_player:
            return

        if value == '+':
            if self.volume < 100:
                self.ui_loggers["status"].debug("Volume up")
                self.volume = (10 * (self.volume // 10)) + 10
                self.ui_loggers["volume"].info(str(self.volume))
                try:
                    self.voice_client.volume = self.volume / 100
                except AttributeError:
                    pass
            else:
                self.ui_loggers["status"].warning("Already at maximum volume")

        elif value == '-':
            if self.volume > 0:
                self.ui_loggers["status"].debug("Volume down")
                self.volume = (10 * ((self.volume + 9) // 10)) - 10
                self.ui_loggers["volume"].info(str(self.volume))
                try:
                    self.voice_client.volume = self.volume / 100
                except AttributeError:
                    pass
            else:
                self.ui_loggers["status"].warning("Already at minimum volume")

        else:
            try:
                value = int(value)
            except ValueError:
                self.ui_loggers["status"].error("Volume argument must be +, -, or a %")
            else:
                if 0 <= value <= 200:
                    self.ui_loggers["status"].debug("Setting volume")
                    self.volume = value
                    self.ui_loggers["volume"].info(str(self.volume))
                    try:
                        self.voice_client.volume = self.volume / 100
                    except AttributeError:
                        pass
                else:
                    self.ui_loggers["status"].error("Volume must be between 0 and 200")

        self.push_volume()

    async def movetext(self,
                       channel: discord.TextChannel) -> None:
        """Moves the embed message to a new channel; can also be used to move the musicplayer to the front.

        Args:
            channel (discord.TextChannel): The channel to move to.
        """

        await self.embed.delete()

        self.embed.channel = channel
        await self.embed.send()
        asyncio.ensure_future(self.add_reactions())

        self.ui_loggers["status"].info("Moved to front")

    async def movevoice(self,
                        voice_channel: discord.VoiceChannel) -> None:
        """Moves the voice client to a new channel.

        Args:
            voice_channel (discord.VoiceChannel): The channel to move to.
        """

        if not self.ready_player:
            return

        # Disconnect
        if self.voice_client:
            try:
                await self.voice_client.disconnect()
            except Exception as e:
                logger.exception(e)

        # Reconnect
        self.ready_player = False
        self.busy_player = True

        self.ready_voice = False
        await self.voice_setup(voice_channel)

        if self.ready_voice:
            self.busy_player = False
            self.ready_player = True
            self.ui_loggers["status"].info("Moved to new channel")

            if self.voice_client:
                self.voice_client.stop()

    async def set_topic_channel(self,
                                channel: discord.TextChannel) -> None:
        """Sets the topic channel for this guild.

        Args:
            channel (discord.TextChannel): The channel to set the topic channel to.
        """

        data.edit(self.guild_id, "music", channel.id, ["topic_id"])

        self.topic_channel = channel
        await self.update_topic(self.topic)

        await channel.trigger_typing()
        info_gui = ui_embed.topic_update(channel, self.topic_channel)
        await info_gui.send()

    async def clear_topic_channel(self,
                                  channel: discord.TextChannel) -> None:
        """Clears the topic channel for this guild.

        Args:
            channel (discord.TextChannel): The channel to send updates to.
        """

        try:
            if self.topic_channel:
                await self.topic_channel.edit(topic="")
        except Exception as e:
            logger.exception(e)

        self.topic_channel = None
        logger.debug("Clearing topic channel")

        data.edit(self.guild_id, "music", "", ["topic_id"])

        await channel.trigger_typing()
        info_gui = ui_embed.topic_update(channel, self.topic_channel)
        await info_gui.send()

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
        asyncio.ensure_future(self.add_reactions())

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

    async def add_reactions(self) -> None:
        """Adds the reaction buttons to an embed UI."""

        self.ui_loggers["status"].info("Loading buttons")
        for e in ("⏯", "⏮", "⏹", "⏭", "🔀", "🔉", "🔊"):
            try:
                await self.embed.sent_embed.add_reaction(e)
            except discord.ClientException:
                self.ui_loggers["status"].error("I couldn't add the buttons. Check my permissions.")

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

        # if self.ready_voice:
        #     logger.warning("Attempt to init voice when already init")
        #     return

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

    async def voice_next(self) -> None:
        """Starts playing the next song in the queue."""

        # if self.state != "ready":
        #     logger.error("Attempt to play song from wrong state ('{}'), must be 'ready'.".format(self.state))
        #     return

        # self.state = "starting stream"

        if self.voice_client.is_playing():
            self.voice_client.stop()

        # Queue empty
        if not self.queue:
            self.ready_player = True

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

        self.ui_loggers["status"].debug("Downloading next song")

        song_link = self.queue[0][0]
        song_name = self.queue[0][1]

        self.history.append(self.queue.pop(0))
        while len(self.history) > self.history_max:
            self.history.pop(0)

        song_data = ytdl.extract_info(song_link, download=False)
        if "entries" in song_data:
            song_data = song_data["entries"][0]
        source = discord.FFmpegPCMAudio(song_data["url"], **options_ffmpeg)
        source = discord.PCMVolumeTransformer(source, volume=self.volume/100)

        # UI updates
        self.ui_loggers["nowplaying"].debug(song_name)
        if "uploader" in song_data:
            self.ui_loggers["author"].debug(song_data["uploader"])
        else:
            self.ui_loggers["author"].debug("Unknown")
        self.ui_loggers["source"].debug(api_music.parse_source(song_data))
        self.ui_loggers["time"].debug("TODO")
        self.ui_loggers["status"].debug("Playing {}".format(song_name))
        self.update_queue()
        await self.update_topic("Playing {}".format(song_name))

        # Start play
        self.voice_client.play(source, after=lambda e: self.voice_after_ts(e))

    def voice_after_ts(self,
                       error: Exception) -> None:
        """Called after a song finishes playing.

        Args:
            error (Exception): Exists if the playing stopped because of an error.
        """

        asyncio.run_coroutine_threadsafe(self.voice_after(error), main.client.loop).result()

    async def voice_after(self,
                          error: Exception) -> None:
        """Called after a song finishes playing.

        Args:
            error (Exception): Exists if the playing stopped because of an error.
        """

        if error:
            logger.exception(error)
            self.ui_loggers["status"].debug("Music player encountered an error")

            try:
                self.voice_client.stop()
            except discord.DiscordException:
                pass

        # self.state = "ready"
        await self.voice_next()

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
