"""
Discord bot that can play audio from YouTube links or search queries. Uses yt-dlp to extract audio URLs and FFmpeg for streaming.
Commands:
- /play [song_query]: Plays the specified song or adds it to the queue.
- /pause: Pauses the currently playing audio.
- /resume: Resumes paused audio.
- /skip: Skips the currently playing song.
- /stop: Stops playback and clears the queue.
- /loop [enable]: Toggles looping of the current playlist on or off.
"""
import os
import logging 
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import asyncio
from collections import deque

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN") # Make sure to set this in your .env file.

GUILD_ID = 820497690609713153 # Set this to the ID of your server if you want to limit the bot to a single server. Otherwise, set to None and it will work in any server it's invited to.

SONG_QUEUES = {} # Dictionary to hold song queues for each guild, keyed by guild ID.
LOOP_STATES = {}  # Set to True to enable looping of the current playlist, keyed by guild ID.

"""
Helper function to run yt-dlp extraction in a non-blocking way using asyncio's run_in_executor. This allows the bot to remain responsive while fetching video information.

Args:
    query: The search query or URL to extract information from.
    ydl_opts: Options to pass to yt-dlp for extraction.
Returns:
    The extracted information from yt-dlp.
"""
async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop() # Get the current event loop to run the blocking yt-dlp extraction in a separate thread.
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts)) # Run the _extract function in a separate thread to avoid blocking the main event loop.

"""
Helper function to perform the actual yt-dlp extraction. This is run in a separate thread to avoid blocking the main event loop.

Args:
    query: The search query or URL to extract information from.
    ydl_opts: Options to pass to yt-dlp for extraction.

Returns:
    The extracted information from yt-dlp.
"""
def _extract(query, ydl_opts):
    # Use yt-dlp to extract information based on the provided query and options. 
    # The download=False option tells yt-dlp not to download the video, but just to extract the information.
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False)

# Sets ups the intents for the bot
intents = discord.Intents.default() 
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

""" Event handler that is called when the bot is ready. It syncs the command tree with the specified guild (if GUILD_ID is set) and prints a message to the console. """
@bot.event
async def on_ready():
    # If GUILD_ID is set, sync the command tree to that specific guild for faster command registration. 
    # Otherwise, it will be registered globally, which can take up to an hour to propagate.
    if GUILD_ID is not None:
        test_guild = discord.Object(id=GUILD_ID)
        await bot.tree.sync(guild=test_guild)
    else:
        await bot.tree.sync()
    print(f"{bot.user} is online")

# Uncomment this if you want to see the guild ID of messages for debugging purposes.
# @bot.event 
# async def on_message(msg):
#     print(msg.guild.id)

"""
Command to play audio based on a search query. It checks if the user is in a voice channel, connects to it if necessary, and uses yt-dlp to search for the song. 
The audio URL is then added to the queue, and if nothing is currently playing, it starts playback immediately.

Args:
    interaction: The Discord interaction object representing the command invocation.
    song_query: The search query for the song to play.
"""
@bot.tree.command(name="play", description="Plays audio or add it to the queue.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    try:
        await interaction.response.defer() # Defer the response to allow time for processing

        voice_channel = interaction.user.voice.channel

        # Check if the user is in a voice channel
        if voice_channel is None:
            await interaction.followup.send("You must be in a voice channel.")
            return

        voice_client = interaction.guild.voice_client

        # Connect to the voice channel if not already connected, or move if connected to a different channel
        if voice_client is None:
            voice_client = await voice_channel.connect()
        elif voice_channel != voice_client.channel:
            await voice_client.move_to(voice_channel)

        # Set up yt-dlp options for extracting audio information. 
        # These options specify that we want the best audio format, no playlists, and to suppress output and warnings.
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0", 
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web_creator"],
                    "player_skip": ["webpage", "configs"],
                }
            },
        }

        # Use yt-dlp to search for the song based on the provided query. 
        # The "ytsearch1:" prefix tells yt-dlp to perform a search and return the first result.
        query = "ytsearch1: " + song_query
        results = await search_ytdlp_async(query, ydl_options)
        tracks = results.get("entries", [])

        # Check if any tracks were found. If not, send a message to the user indicating that no results were found.
        if tracks is None:
            await interaction.followup.send("No results found.")
            return

        # Get the first track from the search results and extract the audio URL and title. 
        # The title is used for display purposes in the queue and now playing messages.
        first_track = tracks[0]
        audio_url = first_track["url"]
        title = first_track.get("title", "Untitled")

        # Add the audio URL and title to the queue for the guild. If there is no existing queue for the guild, a new deque is created to hold the songs.
        guild_id = str(interaction.guild_id)
        if SONG_QUEUES.get(guild_id) is None:
            SONG_QUEUES[guild_id] = deque()

        SONG_QUEUES[guild_id].append((audio_url, title)) # Add the audio URL and title as a tuple to the end of the queue for the guild.

        # If there is currently audio playing or paused, send a message indicating that the song was added to the queue. 
        # Otherwise, start playback immediately and send a message indicating that the song is now playing.
        if voice_client.is_playing() or voice_client.is_paused():
            await interaction.followup.send(f"Added to queue: **{title}**")
        else:
            await interaction.followup.send(f"Now playing: **{title}**")
            await play_next_song(voice_client, guild_id, interaction.channel)
    except Exception as e:
        # Log the exception with a message to help identify where the error occurred. 
        # This will print the stack trace to the console, which can be helpful for debugging.
        logging.exception(f"Error in play command: {e}")
        await interaction.followup.send("An error occurred while trying to play the audio. Try again later.")

""" 
Command to pause the currently playing audio. It checks if there is an active voice client and if it is currently playing audio before pausing. 

Args:
    interaction: The Discord interaction object representing the command invocation.
"""
@bot.tree.command(name="pause", description="Pauses the audio.")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if there is an active voice client and if it is currently playing audio before pausing. 
    # If there is no audio playing, send a message to the user indicating that there is nothing to pause.
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("Pausing audio...")
    else:
        await interaction.response.send_message("No audio is playing")

""" 
Command to resume paused audio. It checks if there is an active voice client and if it is currently paused before resuming. 

Args:
    interaction: The Discord interaction object representing the command invocation.
"""
@bot.tree.command(name="resume", description="Resumes the audio.")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if there is an active voice client and if it is currently paused before resuming.
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("Resuming audio...")
    else:
        await interaction.response.send_message("No audio is paused")

"""
Command to skip the currently playing song. It checks if there is an active voice client and if it is currently playing or paused before 
stopping the current audio, which triggers the next song in the queue to play.

Args:
    interaction: The Discord interaction object representing the command invocation.
"""
@bot.tree.command(name="skip", description="Skips the song currently playing.")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if there is an active voice client and if it is currently playing or paused before stopping the current audio.
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("Skipped the current song")
    else:
        await interaction.response.send_message("No audio is playing")

"""
Command to stop playback and clear the queue. It checks if there is an active voice client and if it is currently playing or paused before 
stopping the audio, clearing the queue, and disconnecting from the voice channel.

Args:
    interaction: The Discord interaction object representing the command invocation.
"""
@bot.tree.command(name="stop", description="Stops the bot.")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client

    # Check if there is an active voice client and if it is currently playing or paused before stopping the audio, clearing the queue, and disconnecting from the voice channel.
    if voice_client:
        guild_id = str(interaction.guild_id)
        voice_client.stop()
        voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()
        await interaction.response.send_message("Stopping...")
    else:
        await interaction.response.send_message("No audio is playing.")

"""
Command to toggle looping of the current playlist queue on or off. When enabled, the current song will be re-added to the end of the queue after it finishes playing, creating a loop effect.

Args:
    interaction: The Discord interaction object representing the command invocation.
    enable: A boolean parameter that determines whether looping should be enabled (True) or disabled (False).
"""
@bot.tree.command(name="loop", description="Loops the current playlist when enabled.")
@app_commands.describe(enable="Enable or disable looping (True/False)")
async def set_loop(interaction: discord.Interaction, enable: bool):
    # Store the looping state for the guild in the LOOP_QUEUES dictionary, keyed by guild ID. 
    # This allows us to check the looping state when a song finishes playing and decide whether to re-add it to the queue.
    guild_id = str(interaction.guild_id)
    LOOP_STATES[guild_id] = enable
    await interaction.response.send_message(f"Looping is now {'enabled' if enable else 'disabled'}")

"""
Command to display the current playlist queue.

Args:
    interaction: The Discord interaction object representing the command invocation.
"""
@bot.tree.command(name="queuelist", description="Displays the current playlist.")
async def print_queue(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)

    # Check if there are songs in the queue for the guild. 
    # If there are, construct a message listing the titles of the songs in the queue. 
    if SONG_QUEUES.get(guild_id):
        queue = ""
        for url, title in SONG_QUEUES[guild_id]:
            queue += f"- **{title}**\n"
        await interaction.response.send_message("*Queue:*\n-----\n" + queue)
    else:
        await interaction.response.send_message("The queue is currently empty.")

"""
Helper function to play the next song in the queue. It checks if looping is enabled and re-adds the current song to the end of the queue if it is.

Args:
    voice_client: The active voice client to use for playback.
    guild_id: The ID of the guild for which to play the next song.
    channel: The text channel to send playback notifications to.
"""
async def play_next_song(voice_client, guild_id, channel):
    # Check if there are songs in the queue for the guild. If there are, play the next song. 
    # If looping is enabled, re-add the current song to the end of the queue before playing the next one.
    if SONG_QUEUES[guild_id]:
        audio_url, title = SONG_QUEUES[guild_id].popleft()

        if LOOP_STATES.get(guild_id) and LOOP_STATES[guild_id]:
            SONG_QUEUES[guild_id].append((audio_url, title)) # Re-add the current song to the end of the queue if looping is enabled.

        # Set up FFmpeg options for streaming the audio.
        ffmpeg_options = {
            "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_on_network_error 1",
            "options": "-vn",
        }

        # Create a new FFmpegOpusAudio source using the extracted audio URL and the specified options. 
        # The executable path is set to the location of the FFmpeg binary.
        source = discord.FFmpegOpusAudio(audio_url, **ffmpeg_options, executable="bin\\ffmpeg\\ffmpeg.exe")

        # Define a callback function to be called after the current song finishes playing. 
        # This function checks for errors and then calls play_next_song again to play the next song in the queue.
        def after_play(error):
            if error:
                print(f"Error playing {title}: {error}")
            asyncio.run_coroutine_threadsafe(play_next_song(voice_client, guild_id, channel), bot.loop)

        # Start playing the audio source using the voice client and set the after_play function as the callback to be called when the audio finishes.
        voice_client.play(source, after=after_play)
        asyncio.create_task(channel.send(f"Now playing: **{title}**"))
    else:
        # If the queue is empty after the current song finishes, disconnect from the voice channel and clear the queue for the guild.
        await voice_client.disconnect()
        SONG_QUEUES[guild_id] = deque()

bot.run(TOKEN) # Runs bot using TOKEN from .env file.
