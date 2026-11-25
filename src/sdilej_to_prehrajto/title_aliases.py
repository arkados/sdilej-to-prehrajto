"""Enrich unsearchable catalog titles with names from the same TMDB movie."""
from __future__ import annotations

import requests

from .sdilej import slugify


def translated_titles(session: requests.Session, movie_id: int, key: str) -> list[str]:
    response = session.get(
        f"https://api.themoviedb.org/3/movie/{int(movie_id)}",
        params={"language": "en-US", "append_to_response": "translations,alternative_titles", "api_key": key},
        timeout=20,
    )
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        # Do not include the credential-bearing request URL in an exception.
        raise RuntimeError(f"TMDB movie {movie_id} returned HTTP {response.status_code}")
    movie = response.json()
    if movie.get("id") != movie_id:
        raise ValueError(f"Unexpected TMDB identity for movie {movie_id}")
    translations = {
        (row["iso_639_1"], row["iso_3166_1"]): row["data"].get("title")
        for row in movie["translations"]["translations"]
    }
    names = (translations.get(("cs", "CZ")), movie.get("title"),
             translations.get(("sk", "SK")), translations.get(("en", "GB")))
    by_slug = {}
    for name in names:
        if name and slugify(name):
            by_slug.setdefault(slugify(name), name.strip())
    if not by_slug:
        for row in movie["alternative_titles"]["titles"]:
            name = row["title"].strip()
            if slugify(name):
                by_slug.setdefault(slugify(name), name)
    return list(by_slug.values())[:4]


def enrich_titles(rows, existing, api_key):
    """Reuse aliases by movie ID on later exports, even without API access."""
    cached = {row["tmdb_id"]: row["search_titles"] for row in existing
              if row.get("tmdb_id") and row.get("search_titles")
              and not any(slugify(s) for s in (row["title"], row.get("original_title")) if s)
              and any(slugify(s) for s in row["search_titles"])}
    count = 0
    with requests.Session() as session:
        for row in rows:
            if any(slugify(s) for s in row["search_titles"]) or not row.get("tmdb_id"):
                continue
            movie_id = row["tmdb_id"]
            names = cached.get(movie_id)
            if names is None and api_key:
                try:
                    names = translated_titles(session, movie_id, api_key)
                except requests.RequestException:
                    raise RuntimeError(f"TMDB connection failed for movie {movie_id}") from None
                cached[movie_id] = names
            if names:
                row["search_titles"] = list(dict.fromkeys([*row["search_titles"], *names]))
                count += 1
    return count
