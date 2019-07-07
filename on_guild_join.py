from . import _musicplayer, _data


async def on_guild_join(guild):
    # Lock on to guild
    _data.cache[guild.id] = _musicplayer.MusicPlayer(guild.id)
