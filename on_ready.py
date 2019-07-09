from modis import main
from . import _musicplayer, _data


async def on_ready():
    # _musicplayer.clear_cache_root()
    for voice_client in main.client.voice_clients:
        await voice_client.disconnect(force=True)
