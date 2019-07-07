from . import _data


async def on_guild_remove(guild):
    # Remove guild lock
    _data.cache.pop(guild.id, None)
