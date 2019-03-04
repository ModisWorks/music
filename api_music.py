import logging
import random
from urllib.parse import urlparse

import soundcloud.resource

from modis.tools import api

logger = logging.getLogger(__name__)

SOURCE_TO_NAME = {
    "soundcloud": "SoundCloud",
    "twitchstream": "Twitch",
    "youtube": "YouTube"
}


# Main parser
def parse_query(query, parse_logger):
    """Gets a list of media from a query, parsing links and search queries and playlists.

    Args:
        query (str): The search query.
        parse_logger (logging.logger): The logger to log parsing feedback to.
    """

    # Check query exists
    args = query.split(' ')
    if len(args) == 0:
        parse_logger.error("No query given")
        return

    # Check whether query is a URL
    parsed_url = urlparse(query)
    if parsed_url and parsed_url.scheme and parsed_url.netloc:
        if "youtube" in parsed_url.netloc and parsed_url.query:
            # URL is a YouTube URL
            return _url_youtube(query, parsed_url, parse_logger)
        if "soundcloud" in parsed_url.netloc:
            # URL is a SoundCloud URL
            return _url_soundcloud(query, parsed_url, parse_logger)
        if "spotify" in parsed_url.netloc:
            # URL is a Spotify URL
            return _url_spotify(query, parsed_url, parse_logger)
        else:
            # URL is not natively supported
            parse_logger.info("URL not natively supported; falling back to direct URL")
            return [(query, query)]

    # Check whether query is a Spotify URI
    parsed_uri = query.split(":")
    if parsed_uri[0].lower() == "spotify":
        if api.client_spotify is None:
            parse_logger.error("Host does not support Spotify")
            return

        try:
            if len(parsed_uri) > 2 and parsed_uri[1] in ["album", "artist", "track", "user"]:
                query_type = parsed_uri[1].lower()
                query_search = ' '.join(parsed_uri[2:])
            else:
                parse_logger.error("Error malformed Spotify URI/URL")
                return

            query_type = query_type.replace('user', 'playlist')
            spotify_tracks = get_sp_results(query_type, query_search)
            logger.debug("Queueing Youtube search: {}".format(spotify_tracks))

            get_ytvideos_from_list(spotify_tracks)
            parse_logger.info("Queued Spotify {} URI: {}".format(query_type, query_search))
            return
    # This sends the track name and artist found with the spotifyAPI to youtube
        except Exception as e:
            logger.exception(e)
            parse_logger.error("Error queueing from Spotify")
            return

    # Check whether SoundCloud specified in aux
    elif args[0].lower() in ["sc", "soundcloud"]:
        if api.client_soundcloud is None:
            parse_logger.error("Host does not support SoundCloud")
            return

        try:
            requests = ['song', 'songs', 'track', 'tracks', 'user', 'playlist', 'tagged', 'genre']
            if len(args) > 2 and args[1] in requests:
                query_type = args[1].lower()
                query_search = ' '.join(args[2:])
            else:
                query_type = 'track'
                query_search = ' '.join(args[1:])
            query_type = query_type.replace('song', 'track')
            parse_logger.info("Queueing SoundCloud {}: {}".format(query_type, query_search))
            soundcloud_tracks = search_sc_tracks(query_type, query_search)
            queue_list(queue_song, soundcloud_tracks, index, shuffle)
            parse_logger.info("Queued SoundCloud {}: {}".format(query_type, query_search))
            return
        except Exception as e:
            logger.exception(e)
            parse_logger.error("Could not queue from SoundCloud")
            return

    # Check whether YouTube specified in aux
    elif args[0].lower() in ["yt", "youtube"] and api.client_youtube is not None:
        if api.client_youtube is None:
            parse_logger.error("Host does not support YouTube")
            return

        try:
            query_search = ' '.join(args[1:])
            parse_logger.info("Queued Youtube search: {}".format(query_search))
            yt_songs = get_ytvideos(query_search)
            queue_list(queue_song, yt_songs, index, shuffle)
            return
        except Exception as e:
            logger.exception(e)
            parse_logger.error("Could not queue YouTube search")
            return

    # Search fallback
    elif api.client_youtube is not None:
        parse_logger.info("Queued YouTube search: {}".format(query))

        yt_songs = get_ytvideos(query)
        queue_list(queue_song, yt_songs, index, shuffle)
        return

    # Search fallback failed
    else:
        parse_logger.error("Host does not support YouTube".format(query))
        return


def queue_list(song_func, songs, index, shuffle):
    if shuffle:
        random.shuffle(songs)

    for i in range(0, len(songs)):
        func_index = None
        if index is not None:
            func_index = i + index
        song_func(songs[i], func_index)


# URL parsers
def _url_youtube(query, parsed_url, parse_logger):
    # Make sure YouTube API is built
    if api.client_youtube is False:
        parse_logger.warning("YouTube API not built; falling back to direct URL")
        return [(query, query)]

    # Extract data from YouTube URL
    query_parts = parsed_url.query.split('&')
    yturl_parts = {}
    for q in query_parts:
        s = q.split('=')
        if len(s) < 2:
            continue
        q_name = s[0]
        q_val = '='.join(s[1:])
        if q_name not in yturl_parts:
            yturl_parts[q_name] = q_val

    # Attempt native YouTube queueing
    if "list" in yturl_parts:
        # URL is a YouTube playlist
        parse_logger.info("Queueing YouTube playlist from link")
        return get_queue_from_playlist(yturl_parts["list"])
    elif "v" in yturl_parts:
        # URL is a YouTube video
        parse_logger.info("Queueing YouTube video from link")
        return [("https://www.youtube.com/watch?v={}".format(yturl_parts["v"]), query)]

    # URL is neither a playlist nor a video
    else:
        parse_logger.warning("YouTube URL not recognised; falling back to direct URL")
        return [(query, query)]


def _url_soundcloud(query, parsed_url, parse_logger):
    # Make sure SoundCloud API is built
    if api.client_soundcloud is None:
        parse_logger.warning("SoundCloud API not built; falling back to direct URL")
        return [(query, query)]

    # Extract data from SoundCloud URL
    track_list = []
    result = api.client_soundcloud.get('/resolve', url=query)

    # Check whether URL is a SoundCloud playlist
    if isinstance(result, soundcloud.resource.ResourceList):
        track_list = []
        for r in result:
            tracks = get_sc_tracks(r)
            if tracks is not None:
                for t in tracks:
                    track_list.append(t)

    # Check whether URL is a SoundCloud song
    elif isinstance(result, soundcloud.resource.Resource):
        tracks = get_sc_tracks(result)
        if tracks is not None:
            for t in tracks:
                track_list.append(t)

    if track_list is not None and len(track_list) > 0:
        parse_logger.info("Queueing SoundCloud songs from link")
        return track_list

    else:
        parse_logger.warning("Could not queue using SoundCloud API; falling back to direct URL")
        return [(query, query)]


def _url_spotify(query, parsed_url, parse_logger):
    if api.client_spotify is None:
        parse_logger.error("Spotify API not built; falling back to direct URL")
        return [(query, query)]

    url_to_uri = ("spotify" + parsed_url.path).replace("/", ":")
    parse_query(url_to_uri, parse_logger)
    # TODO unweirdify this
    return


# YouTube parser
def get_ytvideos_from_list(queries, song_func, index):
    """
    Gets either a list of videos from a playlist or a single video, using the
    first result of a YouTube search

    Args:
        queries (list): A list of queries to make
        song_func (func): A function that gets called after every song is added
        index (int): The start index for the song_func
    """

    if queries is None or len(queries) == 0:
        logger.warning("Empty query for YouTube list")
        return

    for i in range(0, len(queries)):
        results = get_ytvideos(queries[i])
        if len(results) > 0:
            func_index = None
            if index is not None:
                func_index = i + index
            song_func(results[0], func_index)


def get_ytvideos(query):
    """
    Gets either a list of videos from a playlist or a single video, using the
    first result of a YouTube search

    Args:
        query (str): The YouTube search query

    Returns:
        queue (list): The items obtained from the YouTube search
    """

    queue = []
    # Search YouTube
    search_result = api.client_youtube.search().list(
            q=query,
            part="id,snippet",
            maxResults=1,
            type="video,playlist"
    ).execute()

    if not search_result["items"]:
        return []

    # Get video/playlist title
    title = search_result["items"][0]["snippet"]["title"]

    # Queue video if video
    if search_result["items"][0]["id"]["kind"] == "youtube#video":
        # Get ID of video
        videoid = search_result["items"][0]["id"]["videoId"]

        # Append video to queue
        queue.append(["https://www.youtube.com/watch?v={}".format(videoid), title])

    # Queue playlist if playlist
    elif search_result["items"][0]["id"]["kind"] == "youtube#playlist":
        queue = get_queue_from_playlist(search_result["items"][0]["id"]["playlistId"])
    return queue


def get_queue_from_playlist(playlistid):
    queue = []
    # Get items in playlist
    playlist = api.client_youtube.playlistItems().list(
            playlistId=playlistid,
            part="snippet",
            maxResults=50
    ).execute()

    # Append videos to queue
    for entry in playlist["items"]:
        videoid = entry["snippet"]["resourceId"]["videoId"]
        songname = entry["snippet"]["title"]
        queue.append(["https://www.youtube.com/watch?v={}".format(videoid), songname])

    # For playlists with more than 50 entries
    if "nextPageToken" in playlist:
        counter = 2

        while "nextPageToken" in playlist:
            counter += 1

            # Get items in next page of playlist
            playlist = api.client_youtube.playlistItems().list(
                    playlistId=playlistid,
                    part="snippet",
                    maxResults=50,
                    pageToken=playlist["nextPageToken"]
            ).execute()

            # Append videos to queue
            for entry in playlist["items"]:
                videoid = entry["snippet"]["resourceId"]["videoId"]
                songname = entry["snippet"]["title"]
                queue.append(["https://www.youtube.com/watch?v={}".format(videoid), songname])

    return queue


# SoundCloud parser
def search_sc_tracks(query_type, query_search):
    results = []
    if query_type == 'track':
        results = api.client_soundcloud.get("/tracks", q=query_search, filter="public", limit=1)
    elif query_type == 'tracks':
        results = api.client_soundcloud.get("/tracks", q=query_search, filter="public", limit=50)
    elif query_type == 'user':
        results = api.client_soundcloud.get("/users", q=query_search, limit=1)
    elif query_type == 'playlist':
        results = api.client_soundcloud.get("/playlists", q=query_search, limit=1)
    elif query_type == 'tagged':
        while ", " in query_search:
            query_search = query_search.replace(", ", ",").strip()
        results = api.client_soundcloud.get("/tracks", tags=query_search, filter="public", limit=50)
    elif query_type == 'genre':
        while ", " in query_search:
            query_search = query_search.replace(", ", ",").strip()
        results = api.client_soundcloud.get("/tracks", genres=query_search, filter="public", limit=50)

    sc_tracks = []
    for r in results:
        result_tracks = get_sc_tracks(r)
        if result_tracks is not None:
            for t in result_tracks:
                sc_tracks.append(t)

    return sc_tracks


def get_sc_tracks(result):
    if result.kind == "track":
        logger.debug("SoundCloud Track {}".format(result.title))
        return [[result.stream_url, result.title]]
    elif result.kind == "user":
        track_list = []
        logger.debug("SoundCloud User {}".format(result.username))
        tracks = api.client_soundcloud.get("/users/{}/tracks".format(result.id), limit=50)
        for t in tracks:
            track_list.append([t.stream_url, t.title])

        return track_list
    elif result.kind == "playlist":
        track_list = []
        logger.debug("SoundCloud Playlist {}".format(result.title))
        playlist = api.client_soundcloud.get("/playlists/{}".format(result.id), limit=50)
        tracks = playlist.tracks
        for t in tracks:
            track_list.append([t["stream_url"], t["title"]])

        return track_list

    return None


# Spotify parser
def sp_nextpage(results, query_type, query):
    while results['next']:
        nextpage = api.client_spotify.next(results)
        return get_sp_tracks(nextpage, query_type, query)
    return (query)


def get_sp_tracks(results, query_type, query):
    if query_type == 'track':
        song_name = results['name'] # gather the name of the song by looking for the tag ['name']
        song_artist = results['artists'][0]['name'] # same as before, might only return the first artist, unsure
        query = ["{} by {}".format(song_name,song_artist)] # joins both results
        return (query)
    elif query_type == 'artist':
        for tracks in results['tracks'][:10]: # finds all tracks in the album
             song_name = tracks['name']
             song_artist = tracks['artists'][0]['name']
             song = ("{} by {}".format(song_name,song_artist))
             query.append(song)
        return sp_nextpage(results, query_type, query)

    elif query_type == 'album':
        for tracks in results['items']: # finds all tracks in the album
             song_name = tracks['name']
             song_artist = tracks['artists'][0]['name']
             song = ("{} by {}".format(song_name,song_artist))
             query.append(song)
        return sp_nextpage(results, query_type, query)

    elif query_type == 'playlist':
        for tracks in results['items']: # finds all tracks in the album
             song_name = tracks['track']['name']
             song_artist = tracks['track']['artists'][0]['name']
             song = ("{} by {}".format(song_name,song_artist))
             query.append(song)
        return sp_nextpage(results, query_type, query)


def get_sp_results(query_type, query_search):
    query = []
    results = None
    if query_type == 'track':
        results = api.client_spotify.track(query_search)

    elif query_type == 'artist':
        results = api.client_spotify.artist_top_tracks(query_search)

    elif query_type == 'album':
        results = api.client_spotify.album_tracks(query_search)

    elif query_type == 'playlist':
        get_username = query_search.split(" ")[0]
        get_playlist = query_search.split(" ")[2]
        results = api.client_spotify.user_playlist_tracks(get_username, get_playlist)

    if results is not None:
        return get_sp_tracks(results, query_type, query)
    else:
        return []


# Utilities
def duration_to_string(duration):
    """
    Converts a duration to a string

    Args:
        duration (int): The duration in seconds to convert

    Returns s (str): The duration as a string
    """

    m, s = divmod(duration, 60)
    h, m = divmod(m, 60)
    return "%d:%02d:%02d" % (h, m, s)


def parse_source(info):
    """
    Parses the source info from an info dict generated by youtube-dl

    Args:
        info (dict): The info dict to parse

    Returns:
        source (str): The source of this song
    """

    if "extractor_key" in info:
        source = info["extractor_key"]
        lower_source = source.lower()

        for key in SOURCE_TO_NAME:
            lower_key = key.lower()
            if lower_source == lower_key:
                source = SOURCE_TO_NAME[lower_key]

        if source != "Generic":
            return source

    if "url" in info and info["url"] is not None:
        p = urlparse(info["url"])
        if p and p.netloc:
            return p.netloc

    return "Unknown"
