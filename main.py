import itertools
import os

import click
import mutagen
from loguru import logger

# import sys
# from datetime import datetime
# from time import sleep


# import spotipy
# from spotipy import oauth2
# from tinytag import TinyTag

# def get_user_data():
#     """Retrieve username and playlist id from arguments"""
#     if len(sys.argv) == 2:
#         return sys.argv[1], ""
#     elif len(sys.argv) == 3:
#         return sys.argv[1], sys.argv[2]
#     else:
#         print(
#             f"Usage:\n\tpython {sys.argv[0]} username [OPTIONAL]playlist_id"
#             "\n\nTo know how to find each, check the README.md or the GitHub page"
#         )
#         sys.exit()


# def connect_to_spotify(username, scope):
#     """Used to obtain the auth_manager and establish a connection to Spotify
#     for the given user.
#     Returns (Spotify object, auth_manager)"""
#     auth_manager = oauth2.SpotifyOAuth(
#         client_id="",
#         client_secret="",
#         redirect_uri="https://localhost/",
#         scope=scope,
#         username=username,
#     )

#     if auth_manager:
#         return (spotipy.Spotify(auth_manager=auth_manager), auth_manager)

#     print(f"Can't get token for {username}")
#     sys.exit()


# def get_auth_token(auth_manager):
#     auth_token = auth_manager.get_cached_token()

#     if auth_token:
#         return auth_token

#     return auth_manager.get_access_token(as_dict=True)


def album_info(track: dict):
    return (
        track.get("album", []),
        track.get("artist", []),
        # track.get("albumartist", []),
        track.get("date", []),
    )


def cluster_albums(tracks, min_tracks=3, same_album_fn=album_info):

    # We guess that a collection "has an album" if it has {min_tracks} tracks
    # from a given album.
    matched_albums = [
        album
        for album, album_tracks in itertools.groupby(
            (e for e in sorted(tracks, key=same_album_fn) if "album" in e),
            key=same_album_fn,
        )
        if len(list(album_tracks)) > min_tracks
    ]

    unmatched_tracks = [
        track for track in tracks if same_album_fn(track) not in matched_albums
    ]

    return matched_albums, unmatched_tracks


def load_track_metadata(music_dir):
    """Recursively reads local files in indicated music_dir.

    Yields a string '{song} - {artist}'.
    """

    tags = []
    for subdir, _, files in os.walk(music_dir):
        for fn in files:
            try:
                audio_info = dict(mutagen.File(f"{subdir}/{fn}", easy=True))
            except TypeError:
                # logger.debug(f"Failed to load [{fn}]")
                pass

            tags.append(audio_info)

    return tags


# def ensure_playlist_exists(playlist_id):
#     try:
#         if not playlist_id:
#             raise Exception
#         sp.user_playlist(username, playlist_id)["id"]
#         return playlist_id
#     except:
#         print(
#             f"\nNo playlist_id provided. Creating a new playlist..."
#             if len(playlist_id) == 0
#             else f"\nThe playlist_id provided did not match any of your "
#             "existing playlists. Creating a new one..."
#         )
#         return create_new_playlist()


# def create_new_playlist():
#     try:
#         date = datetime.now().strftime("%d %b %Y at %H:%M")  # 1 Jan 2020 at 13:30
#         playlist_id = sp.user_playlist_create(
#             username,
#             "SpotifyMatcher",
#             description="Playlist automatically created by SpotifyMatcher "
#             f"from my local files on {date}. "
#             "Try it at https://github.com/BoscoDomingo/SpotifyMatcher!",
#         )["id"]
#         print(f"Find it at: https://open.spotify.com/playlist/{playlist_id}")
#         return playlist_id
#     except:
#         print(
#             "\nWARNING: \n"
#             "There was an error creating the playlist. Please, create one "
#             "manually and paste its id in the terminal, after your username\n"
#         )
#         sys.exit()


# def add_tracks_to_playlist(track_ids):
#     """Add tracks in batches of 100, since that's the limit Spotify has in place"""
#     spotify_limit = 100
#     while len(track_ids) > 0:
#         try:
#             sp.user_playlist_add_tracks(
#                 username, playlist_id, track_ids[:spotify_limit]
#             )
#         except ValueError:  # API rate limit reached
#             sleep(0.2)
#         else:
#             del track_ids[:spotify_limit]


def dig(obj, *keys, error=True):
    keys = list(keys)
    if isinstance(keys[0], list):
        return dig(obj, *keys[0], error=error)

    if (
        isinstance(obj, dict)
        and keys[0] in obj
        or isinstance(obj, list)
        and keys[0] < len(obj)
    ):
        if len(keys) == 1:
            return obj[keys[0]]
        return dig(obj[keys[0]], *keys[1:], error=error)

    if hasattr(obj, keys[0]):
        if len(keys) == 1:
            return getattr(obj, keys[0])
        return dig(getattr(obj, keys[0]), *keys[1:], error=error)

    if error:
        raise KeyError(keys[0])

    return None


@click.command()
@click.argument("music_dir", type=click.Path(exists=True))
@click.option("-u", "--username", default="")
@click.option("-p", "--playlist-id", default="")
@click.option("-s", "--use-spotify", is_flag=True, default=False)
@click.option("--spotify-scope", default="playlist-modify-public user-library-modify")
@click.option(
    "-f", "--failed-matches-filename", default="spotify-matcher.log", type=click.Path()
)
def main(
    music_dir,
    username,
    playlist_id,
    use_spotify,
    spotify_scope,
    failed_matches_filename,
):

    logger.info(f"Searching path: {music_dir}")

    tracks = load_track_metadata(music_dir)
    logger.info(f"Discovered [{len(tracks)}] music files.")

    albums, tracks = cluster_albums(tracks)
    logger.info(
        f"Discovered [{len(albums)}] albums, "
        f"leaving [{len(tracks)}] tracks not associated with albums."
    )

    print(tracks)

    if not use_spotify:
        return

    # sp = None
    # if spotify:
    #     sp, auth_manager = connect_to_spotify(username, scope)

    #     # Needed to get the cached authentication if missing
    #     dummy_search = sp.search("whatever", limit=1)

    #     token_info = get_auth_token(auth_manager)

    # track_ids = []
    # failed_song_names = []
    # searched_songs = 0

    # with open(failed_matches_filename, "w") as failed_matches_file:

    #     for query, song in get_title_and_artist(music_dir):

    #         searched_songs += 1
    #         print(f"{searched_songs}: {song}")

    #         if spotify:
    #             if auth_manager.is_token_expired(token_info):
    #                 token_info = auth_manager.refresh_access_token(
    #                     token_info["refresh_token"]
    #                 )

    #             try:
    #                 result = sp.search(query, limit=3)
    #                 print(result)
    #                 if len(result) > 1:
    #                     print("multiple hits")
    #                 # ["tracks"]["items"][0]["id"]
    #                 1 / 0
    #             except ValueError:
    #                 print("\t*NO MATCH*")
    #                 failed_matches_file.write(f"{song}\n")
    #                 failed_song_names.append(song)
    #             else:
    #                 track_ids.append(result)

    #     success_rate = f"{len(track_ids) / (searched_songs - 1) * 100:.2f}"
    #     print(
    #         f"\n***TOTAL SONGS SEARCHED: {searched_songs}"
    #         f"  TOTAL MATCHES:{len(track_ids)} ({success_rate}%)***\n"
    #     )

    # number_of_matches = len(track_ids)

    # if spotify:
    #     playlist_id = ensure_playlist_exists(playlist_id)
    #     add_tracks_to_playlist(track_ids)

    # print(
    #     f"\nSuccessfully added {number_of_matches} songs to the playlist.\n"
    #     "Thank you for using SpotifyMatcher!"
    # )
    # print(
    #     f"\n{searched_songs-number_of_matches} UNMATCHED SONGS (search "
    #     "for these manually, as they either have wrong info or aren't "
    #     f'available in Spotify)\nWritten to "{failed_matches_filename}":\n'
    # )


if __name__ == "__main__":
    main()
