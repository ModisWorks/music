import logging

import discord

from modis.modules.music import _data, _musicplayer, ui_embed

logger = logging.getLogger(__name__)


async def on_command(root, aux, query, msgobj):
    # Simplify message info
    guild = msgobj.guild
    author = msgobj.author
    channel = msgobj.channel
    content = msgobj.content

    # Remove message
    try:
        await msgobj.delete()
    except discord.errors.NotFound:
        logger.warning("Could not delete music player command message - NotFound")
    except discord.errors.Forbidden:
        logger.warning("Could not delete music player command message - Forbidden")

    # Lock on to guild if not yet locked
    if guild.id not in _data.cache or _data.cache[guild.id].state == 'destroyed':
        _data.cache[guild.id] = _musicplayer.MusicPlayer(guild.id)

    # Commands
    if root == 'play':
        now = "now" in aux
        next = "next" in aux
        shuffle = "shuffle" in aux

        if now or next:
            await _data.cache[guild.id].play(author.voice.channel, channel, query, index=1, stop_current=now, shuffle=shuffle)
        else:
            await _data.cache[guild.id].play(author.voice.channel, channel, query, shuffle=shuffle)
    if root == 'insert':
        # TODO index int check
        await _data.cache[guild.id].play(author.voice.channel, channel, query, index=int(query))
    elif root == 'pause':
        await _data.cache[guild.id].pause()
    elif root == 'resume':
        await _data.cache[guild.id].resume()
    elif root == 'skip':
        await _data.cache[guild.id].skip(query=query)
    elif root == 'remove':
        await _data.cache[guild.id].remove(index=query)
    elif root == 'rewind':
        await _data.cache[guild.id].rewind(query=query)
    elif root == 'restart':
        await _data.cache[guild.id].rewind(query="0")
    elif root == 'shuffle':
        await _data.cache[guild.id].shuffle()
    elif root == 'loop':
        await _data.cache[guild.id].set_loop(query)
    elif root == 'stop':
        await _data.cache[guild.id].stop(log_stop=True)
    elif root == 'volume':
        await _data.cache[guild.id].setvolume(query)
    elif root == 'topic':
        topic_on = "on" in aux
        topic_off = "off" in aux

        if topic_on and topic_off:
            await msgobj.channel.trigger_typing()
            embed = ui_embed.error_message(channel, "Invalid Topic",
                                           "The topic aux command cannot contain both 'on' and 'off'")
            await embed.send()
        elif topic_on:
            await msgobj.channel.trigger_typing()
            await _data.cache[guild.id].set_topic_channel(channel)
        elif topic_off:
            await msgobj.channel.trigger_typing()
            await _data.cache[guild.id].clear_topic_channel(channel)
        else:
            await msgobj.channel.trigger_typing()
            embed = ui_embed.error_message(channel, "Invalid Topic",
                                           "The topic aux command must be either 'on' or 'off'")
            await embed.send()
    elif root == 'nowplaying':
        await _data.cache[guild.id].nowplaying_info(channel)
    elif root == 'destroy':
        await _data.cache[guild.id].destroy()
    elif root == 'front' or root == 'movehere':
        await _data.cache[guild.id].movehere(channel)
    elif root == 'reconnect' or root == 'movevoice':
        await _data.cache[guild.id].movevoice(author.voice.channel)
