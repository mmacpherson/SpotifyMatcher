import datetime
import functools
import os
import shutil
import time
from difflib import SequenceMatcher

import click
import mutagen
import pandas as pd
import spotipy
from loguru import logger


# Helper functions.
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


# Local Files Search
def is_plausible_music_file(fname):

    notmusic_extensions = {".ini", ".jpg", ".bmp", ".m3u", ".db", ".txt"}
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


# Spotify Interaction
def connect_to_spotify(username, scope):
    """Used to obtain the auth_manager and establish a connection to Spotify
    for the given user.
    Returns (Spotify object, auth_manager)"""
    auth_manager = spotipy.oauth2.SpotifyOAuth(
        scope=scope,
        username=username,
    )

    spotify = spotipy.Spotify(auth_manager=auth_manager)

    return spotify


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


def get_unmatched_track_ids(df):
    return ~df.groupby("discovery_id").apply(
        lambda f: (f.album_matched | f.nonalbum_track_matched).any()
    )


def copy_unmatched_tracks(tracks_df, music_dir, unmatched_tracks_dir):

    unmatched_track_ids = get_unmatched_track_ids(tracks_df)
    unmatched_track_ids = unmatched_track_ids[unmatched_track_ids]

    for row in tracks_df.loc[
        lambda f: f.discovery_id.isin(unmatched_track_ids.index)
    ].itertuples():

        from_path = row.path
        to_path = from_path.replace(music_dir, unmatched_tracks_dir)

        to_dir = os.path.dirname(to_path)
        if not os.path.exists(to_dir):
            os.makedirs(to_dir)

        shutil.copy2(from_path, to_path)


@click.command()
@click.argument("username")
@click.argument("music_dir", type=click.Path(exists=True))
@click.option("-p", "--playlist-id", default="")
@click.option("-s", "--use-spotify", is_flag=True, default=False)
@click.option("--spotify-scope", default="playlist-modify-public user-library-modify")
@click.option(
    "-f", "--matches-filename", default="spotify-track-matches.csv", type=click.Path()
)
@click.option("-u", "--unmatched-tracks-dir", default=None, type=click.Path())
def main(
    username,
    music_dir,
    playlist_id,
    use_spotify,
    spotify_scope,
    matches_filename,
    unmatched_tracks_dir,
):

    # Guard against trailing slashes.
    music_dir = music_dir.rstrip("/")
    unmatched_tracks_dir = unmatched_tracks_dir.rstrip("/")

    logger.info(f"Searching path: {music_dir}")

    tracks = load_track_metadata(music_dir)
    logger.info(f"Discovered [{len(tracks)}] music files.")

    if not use_spotify:
        logger.info(
            "Exiting without connecting to spotify, because "
            "`-s/--use-spotify` was set to False."
        )
        return

    spotify = connect_to_spotify(username, spotify_scope)

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
    logger.info(
        f"That leaves [{sum(get_unmatched_track_ids(tracks_df))}] unmatched tracks."
    )

    # Save off matches log.
    tracks_df.to_csv(matches_filename, index=False)

    # Create playlist.
    create_spotify_playlist(spotify, username, tracks_df, playlist_id=playlist_id)

    # Copy unmatched tracks.
    if unmatched_tracks_dir is not None:
        copy_unmatched_tracks(tracks_df, music_dir, unmatched_tracks_dir)


if __name__ == "__main__":
    main()
