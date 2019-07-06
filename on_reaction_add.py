from modis import main

from . import _data

import discord


async def on_reaction_add(reaction, user):
    """The on_message event handler for this module

    Args:
        reaction (discord.Reaction): Input reaction
        user (discord.User): The user that added the reaction
    """

    # Simplify reaction info
    guild = reaction.message.guild
    emoji = reaction.emoji

    # TODO port to new activation
    # if not data.cache["guilds"][guild.id][_data.modulename]["activated"]:
    #     return

    # Commands section
    if user != main.client.user:
        if guild.id not in _data.cache or _data.cache[guild.id].state == 'destroyed':
            return

        try:
            valid_reaction = reaction.message.id == _data.cache[guild.id].embed.sent_embed.id
        except AttributeError:
            pass
        else:
            if valid_reaction:
                # Remove reaction
                try:
                    await reaction.message.remove_reaction(emoji, user)
                except discord.errors.NotFound:
                    pass
                except discord.errors.Forbidden:
                    pass

                # Commands
                if emoji == "⏯":
                    await _data.cache[guild.id].toggle()
                if emoji == "⏹":
                    await _data.cache[guild.id].stop()
                if emoji == "⏭":
                    await _data.cache[guild.id].skip("1")
                if emoji == "⏮":
                    await _data.cache[guild.id].rewind("1")
                if emoji == "🔀":
                    await _data.cache[guild.id].shuffle()
                if emoji == "🔉":
                    await _data.cache[guild.id].setvolume('-')
                if emoji == "🔊":
                    await _data.cache[guild.id].setvolume('+')
