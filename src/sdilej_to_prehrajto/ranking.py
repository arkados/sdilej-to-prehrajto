from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .matching import RELEASE_RUNTIMES, YEAR_RE
from .models import Candidate, Film, LanguageTier, MatchTier


CZECH_CODES = {"cs", "cz", "ces", "cze", "czech"}
SLOVAK_CODES = {"sk", "slk", "slo", "slovak"}
SELECTION_POLICY = "compact-quality-v13"
EFFICIENT_CODECS = {"av1", "h265", "hevc", "x265"}
INEFFICIENT_CODECS = {"h264", "vc1"}


def discovery_policy(film: Film) -> str:
    # Revisit films affected by catalog aliases or documented edition lengths.
    # The selection policy on already verified sources remains independent.
    policy = SELECTION_POLICY
    if any(t not in (film.title, film.original_title) or YEAR_RE.search(t) for t in film.titles):
        digest = hashlib.sha256(json.dumps(film.titles, ensure_ascii=False).encode()).hexdigest()
        policy += f":titles-v1:{digest[:16]}"
    if release_runtime := RELEASE_RUNTIMES.get((film.cr_film_id, film.year)):
        policy += f":release-runtime:{release_runtime}"
    return policy


def infer_video_codec(filename: str | None) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", (filename or "").lower())
    patterns = (
        (r"\b(?:h|x)\s*265\b|\bhevc\b", "h265"),
        (r"\b(?:h|x)\s*264\b|\bavc\b", "h264"),
        (r"\bav\s*1\b", "av1"),
        (r"\bvc\s*1\b", "vc1"),
    )
    for pattern, codec in patterns:
        if re.search(pattern, normalized):
            return codec
    return None


def resolution_rank(width: int, height: int = 0) -> int:
    if width >= 3840 or height >= 2160:
        return 5
    if width >= 2560 or height >= 1440:
        return 4
    # Allow the small encoder padding/cropping differences commonly reported
    # for Full HD sources (for example 1918x808).
    if width >= 1900 or height >= 1060:
        return 3
    if width >= 1280 or height >= 720:
        return 2
    return 1 if width > 0 or height > 0 else 0


def minimum_bitrate_mbps(candidate: Candidate) -> float:
    efficient = candidate.video_codec in EFFICIENT_CODECS
    inefficient = candidate.video_codec in INEFFICIENT_CODECS
    return {
        # Resolution has priority over codec efficiency. Keep only an absolute
        # floor so a compact, complete 4K encode can beat an oversized remux.
        5: 5.0,
        4: 9.0 if inefficient else 5.0,
        3: 2.4 if efficient else 2.75,
        2: 1.5 if efficient else 2.5,
        1: 0.8 if efficient else 1.2,
    }.get(resolution_rank(candidate.width, candidate.height), float("inf"))


def quality_acceptable(candidate: Candidate) -> bool:
    bitrate = candidate.average_bitrate_mbps
    return bitrate is not None and bitrate >= minimum_bitrate_mbps(candidate)


def language_tier(language: str | None) -> LanguageTier:
    normalized = (language or "").strip().lower()
    if normalized in CZECH_CODES:
        return LanguageTier.CZECH_AUDIO
    if normalized in SLOVAK_CODES:
        return LanguageTier.SLOVAK_AUDIO
    if normalized:
        return LanguageTier.FOREIGN_AUDIO
    return LanguageTier.UNKNOWN


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    acceptable = [
        candidate
        for candidate in candidates
        if candidate.match_tier in (MatchTier.STRONG, MatchTier.SOLID)
        and candidate.language_tier != LanguageTier.UNKNOWN
        and candidate.width > 0
        and quality_acceptable(candidate)
    ]
    return sorted(
        acceptable,
        key=lambda candidate: (
            int(candidate.language_tier),
            -resolution_rank(candidate.width, candidate.height),
            candidate.size_bytes or 0,
            candidate.source_id,
        ),
    )


def resolution_label(width: int, height: int = 0) -> str:
    rank = resolution_rank(width, height)
    if rank == 5:
        return "4K"
    if rank == 4:
        return "1440p"
    if rank == 3:
        return "1080p"
    if rank == 2:
        return "720p"
    return "SD"


def _has_latin_letter(title: str) -> bool:
    return any(unicodedata.name(char, "").startswith("LATIN ") for char in title)


def display_name(film: Film, candidate: Candidate, *, preserve_title: bool = False) -> str:
    title = film.title
    # The target can discard every letter of a name written in a non-Latin
    # script. Use another catalog title to keep the upload identifiable.
    if not preserve_title and any(char.isalpha() for char in title) and not _has_latin_letter(title):
        title = next((alias for alias in film.titles if _has_latin_letter(alias)), title)
    base = f"{title} ({film.year})" if film.year else title
    base = f"{base} {resolution_label(candidate.width, candidate.height)}"
    original = (film.original_language or "").lower()
    if candidate.language_tier == LanguageTier.CZECH_AUDIO:
        return base if original in CZECH_CODES else f"{base} CZ Dabing"
    if candidate.language_tier == LanguageTier.SLOVAK_AUDIO:
        return f"{base} SK" if original in SLOVAK_CODES else f"{base} SK Dabing"
    return f"{base} CZ Titulky"
