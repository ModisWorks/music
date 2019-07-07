from modis import main
from . import _musicplayer, _data


async def on_ready():
    _musicplayer.clear_cache_root()

    # Lock on to guild if not yet locked
    for guild in main.client.guilds:
        if guild.id not in _data.cache or _data.cache[guild.id].state == 'destroyed':
            _data.cache[guild.id] = _musicplayer.MusicPlayer(guild.id)
