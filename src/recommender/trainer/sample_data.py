"""Synthetic playlists in the same CSV format as the Spotify dataset used in the course.

The real dataset (a subset of the Spotify Million Playlist Dataset) can't be redistributed,
so CI, local development and demos use generated data. Patterns are planted on purpose:
playlists follow a genre, and inside each genre a few "anchor" tracks almost always appear
together, so the trainer has real rules to find and tests can assert on them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random

COLUMNS = [
    "track_uri",
    "album_name",
    "album_uri",
    "artist_name",
    "artist_uri",
    "duration_ms",
    "pid",
    "track_name",
]
GENRES = ["sertanejo", "mpb", "funk", "rock", "pagode", "eletronica"]
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def fake_uri(kind: str, name: str) -> str:
    """Deterministic 22-character base62 id, shaped like Spotify's but not real."""
    number = int.from_bytes(hashlib.sha256(f"{kind}:{name}".encode()).digest()[:17])
    chars = []
    for _ in range(22):
        number, rem = divmod(number, 62)
        chars.append(_ALPHABET[rem])
    return f"spotify:{kind}:{''.join(chars)}"


def catalogue(tracks_per_genre: int = 40) -> dict[str, list[dict]]:
    tracks: dict[str, list[dict]] = {}
    for genre in GENRES:
        tracks[genre] = []
        for i in range(tracks_per_genre):
            artist = f"{genre.title()} Artist {i % 8 + 1}"
            album = f"{genre.title()} Album {i % 5 + 1}"
            name = f"{genre.title()} Song {i + 1:02d}"
            tracks[genre].append(
                {
                    "track_uri": fake_uri("track", f"{genre}-{i}"),
                    "track_name": name,
                    "artist_name": artist,
                    "artist_uri": fake_uri("artist", artist),
                    "album_name": album,
                    "album_uri": fake_uri("album", album),
                    "duration_ms": 150_000 + (i * 7919) % 120_000,
                }
            )
    return tracks


def anchors(genre: str, tracks: dict[str, list[dict]]) -> list[str]:
    """The planted co-occurring tracks of a genre (first three of its catalogue)."""
    return [t["track_uri"] for t in tracks[genre][:3]]


def generate_rows(playlists: int = 600, *, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not security
    tracks = catalogue()
    rows = []
    for pid in range(playlists):
        genre = rng.choice(GENRES)
        pool = tracks[genre]
        chosen = {t["track_uri"]: t for t in rng.sample(pool[3:], rng.randint(8, 16))}
        # The anchors travel together in ~85% of the genre's playlists.
        if rng.random() < 0.85:
            for t in pool[:3]:
                chosen[t["track_uri"]] = t
        # A little noise from other genres.
        for other in rng.sample(GENRES, 2):
            t = rng.choice(tracks[other])
            chosen[t["track_uri"]] = t
        rows += [{**t, "pid": pid} for t in chosen.values()]
    return rows


def generate_csv(playlists: int = 600, *, seed: int = 7) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(generate_rows(playlists, seed=seed))
    return buffer.getvalue().encode()
