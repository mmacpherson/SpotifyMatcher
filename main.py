import datetime
import functools
import itertools
import os
import time
from difflib import SequenceMatcher

import click
import mutagen
import pandas as pd
import spotipy
from loguru import logger

# import sys
# from datetime import datetime
# from time import sleep


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


def chunk(items, size):
    if len(items) <= size:
        return [items]

    return [items[:size]] + chunk(items[size:], size)


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


def similarity(a, b):
    return (
        SequenceMatcher(None, a, b).ratio() + SequenceMatcher(None, b, a).ratio()
    ) / 2


def connect_to_spotify(username, scope):
    """Used to obtain the auth_manager and establish a connection to Spotify
    for the given user.
    Returns (Spotify object, auth_manager)"""
    auth_manager = spotipy.oauth2.SpotifyOAuth(
        scope=scope,
        username=username,
    )

    spotify = spotipy.Spotify(auth_manager=auth_manager)

    return spotify, auth_manager


# def get_auth_token(auth_manager):
#     auth_token = auth_manager.get_cached_token()

#     if auth_token:
#         return auth_token

#     return auth_manager.get_access_token(as_dict=True)


def album_info(track: dict):
    return (
        track.get("album", ""),
        track.get("artist", ""),
        # track.get("albumartist", None),
        track.get("date", ""),
    )


def cluster_albums(tracks, min_tracks=3, same_album_fn=album_info):

    # We guess that a collection "has an album" if it has {min_tracks} tracks
    # from a given album.
    matched_albums = [
        (album, album_tracks)
        for album, album_tracks in (
            (album, list(album_tracks))
            for album, album_tracks in itertools.groupby(
                (e for e in sorted(tracks, key=same_album_fn) if "album" in e),
                key=same_album_fn,
            )
        )
        if len(album_tracks) > min_tracks
    ]

    unmatched_tracks = [
        track
        for track in tracks
        if same_album_fn(track) not in [a for (a, b) in matched_albums]
    ]

    return matched_albums, unmatched_tracks


def is_plausible_music_file(fname):

    notmusic_extensions = {".ini", ".jpg", ".bmp", ".m3u", ".db"}
    return not any(fname.endswith(e) for e in notmusic_extensions)


def collapse_singletons(info):

    return {
        k: v[0] if (isinstance(v, list) and (len(v) == 1)) else ",".join(map(str, v))
        for (k, v) in info.items()
    }


def load_track_metadata(music_dir):
    """Recursively reads local files in indicated music_dir.

    Yields a string '{song} - {artist}'.
    """

    fields_to_keep = {"discovery_id", "album", "title", "artist", "date", "path"}

    discovery_id = 0
    tracks = []
    for subdir, _, files in os.walk(music_dir):
        for fn in files:
            if not is_plausible_music_file(fn):
                continue
            try:
                path = f"{subdir}/{fn}"
                audio_info = dict(mutagen.File(path, easy=True))
            except TypeError:
                logger.debug(f"Failed to load [{fn}] with TypeError.")
                pass
            except Exception as e:
                logger.debug(f"Failed to load [{fn}] with exception [{e}].")
                pass

            track = {"discovery_id": discovery_id, "path": path} | collapse_singletons(
                audio_info
            )
            track = {k: v for k, v in track.items() if k in fields_to_keep}
            tracks.append(track)

            discovery_id += 1

    return tracks


def expand_spotify_albums(spotify, albums):

    track_ids = []
    for album, album_tracks in sorted(albums, key=lambda e: e[0][-1]):

        album_title, artist, date = album

        album_title = album_title[0]
        artist = artist[0]

        album_result = spotify.search(
            q=f"artist:{artist} album:{album_title}", type="album"
        )

        nhits = dig(album_result, "albums", "total")
        if nhits == 0:
            logger.info(f"No hits for album [{album}].")
            continue

        if nhits > 1:
            logger.info(f"Multiple hits for album [{album}].")
            continue

        # Look up tracks on matching album.
        track_ids.extend(
            [
                e["id"]
                for e in spotify.album_tracks(
                    dig(album_result, "albums", "items", 0, "id")
                )["items"]
            ]
        )

    return track_ids


def match_spotify_tracks(spotify, tracks):

    track_ids = []
    for track in tracks:

        try:
            track_result = spotify.search(q=f"{dig(track, 'title', 0)}", type="track")
        except KeyError:
            logger.info(f"No title available for track [{track}].")
            continue

        nhits = dig(track_result, "tracks", "total")
        if nhits == 0:
            logger.info(f"No hits for track [{track}].")
            continue

        if nhits > 1:
            logger.info(f"Multiple hits for track [{track}].")
            continue

        track_ids.append(dig(track_result, "tracks", "items", 0, "id"))

    return track_ids


SEARCH_PAIRS = (
    ("title", "track", 1),
    ("artist", "artist", 0.5),
    ("album", "album", 0.5),
    # ("date", "year"),
)


def spotify_artists_to_string(artists):
    return ",".join(e["name"] for e in artists)


def spotify_process_track(spotify_hit):

    return dict(
        album=dig(spotify_hit, "album", "name"),
        album_id=dig(spotify_hit, "album", "id"),
        track_id=spotify_hit["id"],
        track=spotify_hit["name"],
        popularity=spotify_hit["popularity"],
        artist=spotify_artists_to_string(spotify_hit["artists"]),
    )


def track_similarity(track, spotify_hit, search_pairs=SEARCH_PAIRS):

    numerator, denominator = 0, 0
    for (q, s, w) in search_pairs:
        if q not in track:
            continue
        if s not in spotify_hit:
            continue
        numerator += w * similarity(track[q], spotify_hit[s])
        denominator += w

    return numerator / denominator


def spotify_match_track(spotify, track, search_pairs=SEARCH_PAIRS):

    search_clauses = []
    for (q, s, _) in search_pairs:
        if q not in track:
            continue
        search_clauses += [f"{s}:{track[q]}"]

    query = " ".join(search_clauses).strip()

    if not query:
        return []

    return sorted(
        (
            e | {"similarity": track_similarity(track, e)}
            for e in (
                spotify_process_track(t)
                for t in dig(spotify.search(q=query, type="track"), "tracks", "items")
            )
        ),
        key=lambda e: (-1.0 * e["similarity"], -1.0 * e["popularity"]),
    )


def spotify_batch_match_tracks(spotify, tracks, chunksize=25, sleep_seconds=1):

    records = []
    chunks = chunk(tracks, chunksize)
    for ix, track_chunk in enumerate(chunks):
        logger.info(
            f"On chunk: [{ix} / {len(chunks)}]  Current num records: [{len(records)}]"
        )
        for t in track_chunk:
            try:
                matches = spotify_match_track(spotify, t)
            except spotipy.SpotifyException as e:
                logger.warn("SpotifyException thrown: [{}]", e)
                matches = []

            # Empty matches arises from exception above, or just no hits.
            if not matches:
                records += [t]
                continue

            records.extend([t | {f"s_{a}": b for (a, b) in e.items()} for e in matches])

            time.sleep(sleep_seconds)

    return records


def select_albums(df, album_threshold=3):

    odf = df.copy()
    while True:

        # Select album with highest average score.
        candidate_albums = (
            odf.loc[lambda f: ~f.album_matched & ~f.album_track_mask]
            .groupby("s_album_id")
            .filter(lambda g: len(g) >= album_threshold)
            .groupby("s_album_id")
            .s_similarity.agg(["mean", "count"])
            .sort_values("mean", ascending=False)
        )

        if not len(candidate_albums):
            break

        selected_album = candidate_albums.index[0]

        # Mask out tracks that belong to this album (by setting `matched` to
        # True).
        subtended_tracks = set(
            odf.loc[lambda f: f.s_album_id == selected_album].discovery_id.tolist()
        )

        odf = odf.assign(
            album_matched=lambda f: f.album_matched | (f.s_album_id == selected_album),
            album_track_mask=lambda f: f.album_track_mask
            | f.discovery_id.isin(subtended_tracks),
        )

    # Assumes we've sorted descending by similarity within track in advance.
    return odf.assign(
        nonalbum_track_matched=lambda f: ~f.album_matched
        & ~f.album_track_mask
        & f.s_track_id.notnull()
        & ~f.duplicated(subset="discovery_id", keep="first")
    )


def create_spotify_playlist(
    spotify, username, tracks_df, playlist_id="", batch_size=50, sleep_seconds=0.50
):

    if not playlist_id:
        date = datetime.datetime.now().strftime(
            "%d %b %Y at %H:%M"
        )  # 1 Jan 2020 at 13:30
        playlist_id = spotify.user_playlist_create(
            username,
            "SpotifyMatcher",
            # public=False,
            description=f"Playlist automatically created by SpotifyMatcher on {date}.",
        )["id"]

    # Convert albums into tracks.
    album_track_ids = functools.reduce(
        lambda a, b: a + b,
        (
            [e["id"] for e in spotify.album_tracks(aid)["items"]]
            for aid in tracks_df.loc[lambda f: f.album_matched]
            .s_album_id.unique()
            .tolist()
        ),
    )

    nonalbum_track_ids = (
        tracks_df.loc[lambda f: f.nonalbum_track_matched].s_track_id.unique().tolist()
    )
    for _ids in chunk(album_track_ids + nonalbum_track_ids, batch_size):

        spotify.user_playlist_add_tracks(username, playlist_id, _ids)
        time.sleep(sleep_seconds)

    return playlist_id


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


@click.command()
@click.argument("username")
@click.argument("music_dir", type=click.Path(exists=True))
@click.option("-p", "--playlist-id", default="")
@click.option("-s", "--use-spotify", is_flag=True, default=False)
@click.option("--spotify-scope", default="playlist-modify-public user-library-modify")
@click.option(
    "-f", "--failed-matches-filename", default="spotify-matcher.log", type=click.Path()
)
def main(
    username,
    music_dir,
    playlist_id,
    use_spotify,
    spotify_scope,
    failed_matches_filename,
):

    logger.info(f"Searching path: {music_dir}")

    tracks = load_track_metadata(music_dir)
    logger.info(f"Discovered [{len(tracks)}] music files.")

    # albums, tracks = cluster_albums(tracks)
    # logger.info(
    #     f"Discovered [{len(albums)}] albums, "
    #     f"leaving [{len(tracks)}] tracks not associated with albums."
    # )

    if not use_spotify:
        return

    spotify, auth_manager = connect_to_spotify(username, spotify_scope)

    # Search spotify for each track in collection.
    tracks_df = pd.DataFrame(spotify_batch_match_tracks(spotify, tracks)).assign(
        album_matched=False, album_track_mask=False
    )
    logger.info(f"Found [{len(tracks_df)}] potentially-matching tracks at Spotify.")

    # Attempt to fish out whole albums from among matched tracks.
    tracks_df = select_albums(tracks_df)
    logger.info(
        f"Found [{len(tracks_df.loc[lambda f: f.album_matched].s_album_id.unique())}]"
        " whole albums among tracks."
    )
    logger.info(
        f"Found [{len(tracks_df.loc[lambda f: f.nonalbum_track_matched])}]"
        " matching tracks outside albums."
    )
    num_unmatched = sum(
        ~tracks_df.groupby("discovery_id").apply(
            lambda f: (f.album_matched | f.nonalbum_track_matched).any()
        )
    )
    logger.info(f"That leaves [{num_unmatched}] unmatched tracks.")

    tracks_df.to_csv("matched-tracks.csv", index=False)

    # Take tracks that match at spotify, but are not associated with an album.
    create_spotify_playlist(spotify, username, tracks_df, playlist_id=playlist_id)

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
