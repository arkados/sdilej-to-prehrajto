from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

from .models import Film, MatchTier


YEAR_TOLERANCE = 0
RUNTIME_TOLERANCE = 0.05
RUNTIME_SHORTFALL_REJECT = 0.05
RUNTIME_HARD_REJECT = 0.40
SIMILARITY_GATE = 0.58
# Published broadcast and streaming editions can differ from the archive's theatrical
# runtime. Apply these only to a title with an explicit matching year.
RELEASE_RUNTIMES = {
    # https://sede.mcu.gob.es/CatalogoICAA/es-es/Peliculas/Detalle?pelicula=153715&q=true
    (484, 2015): 168,
    # https://www.ceskatelevize.cz/porady/26920-mrtva-a-ziva/
    (10690, 1940): 126,
    # https://www.ceskatelevize.cz/porady/1142247383-osud-cloveka/
    (10790, 1959): 97,
    # https://www.ceskatelevize.cz/porady/16759-norimbersky-proces/
    (10807, 1961): 172,
    # https://www.fsk.de/detail/?detailid=63c7b11d4deb09001c8fb3f9&stype=single
    (21113, 1961): 104,
    # https://www.sicvenezia.eu/4727/edizione-2018/film-2018/tumbbad/?lang=en
    (29724, 2018): 104,
    # https://www.legislation.gov.au/files/gazettes/historic/1990/1990GN15.pdf
    (32455, 1989): 100,
    # https://www.ceskatelevize.cz/porady/15988-synove-hor/
    (24064, 1956): 82,
    # https://www.iprima.cz/filmy/deti-na-zakazku
    (12568, 1938): 69,
    # https://www.ceskatelevize.cz/porady/12886173225-balada-o-vojakovi/
    (21551, 1959): 85,
    # https://www.disneyplus.com/cs-cz/browse/entity-1d8f1a4a-72dc-4532-a0e0-d35daa00d3a7
    (20410, 2025): 84,
}
EPISODE_RE = re.compile(r"\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{1,3}\b", re.I)
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
ENCODED_YEAR_RE = re.compile(
    r"(?<![^\W_])(?:2O[0-9O]{2}|(?:19|20)(?:O[0-9O]|[0-9]O))(?![^\W_])",
    re.I,
)
RELEASE_NOISE_RE = re.compile(
    r"\b(?:4320p|2160p|1440p|1080p|720p|576p|480p|4k|uhd|full[ ._-]?hd|"
    r"bluray|bdrip|webrip|web[ ._-]?dl|hdrip|dvdrip|hdtv|remux|"
    r"cz(?:ech)?|cs|sk|fra|fre|eng|dab(?:ing)?|tit(?:ulky)?|subs?|"
    r"x26[45]|h26[45]|hevc|avc|ac3|dts|aac|mkv|mp4|avi|mpg)\b",
    re.I,
)


def title_words(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().replace("&", " a ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def normalize_title(value: str, *, keep_years: bool = False) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower().replace("&", " a ")
    if not keep_years:
        value = YEAR_RE.sub(" ", value)
    value = RELEASE_NOISE_RE.sub(" ", value)
    value = re.sub(r"\b(?:5[ ._-]?1|7[ ._-]?1|\d+ch)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def title_and_release_year(candidate: str, aliases: tuple[str, ...]) -> tuple[str, int | None, list[str]]:
    # Remove release years from the compared title, preserving year-shaped
    # tokens only when the complete catalog alias occurs at word boundaries.
    candidate = ENCODED_YEAR_RE.sub(
        lambda match: match.group().upper().replace("O", "0"), candidate
    )
    if not any(YEAR_RE.search(alias) for alias in aliases):
        year_match = YEAR_RE.search(candidate)
        names = list(filter(None, (normalize_title(alias) for alias in aliases)))
        return normalize_title(candidate), int(year_match.group()) if year_match else None, names

    words = title_words(candidate)
    title_numbers = set()
    usable = []
    for alias in aliases:
        name = title_words(alias)
        if not name:
            continue
        if YEAR_RE.search(name):
            span = re.search(rf"\b{re.escape(name)}\b", words)
            if not span:
                continue
            title_numbers.update(m.start() for m in YEAR_RE.finditer(words, span.start(), span.end()))
        if cleaned := normalize_title(name, keep_years=True):
            usable.append(cleaned)
    original_words = words
    releases = [m for m in YEAR_RE.finditer(words) if m.start() not in title_numbers]
    year = int(releases[0].group()) if releases else None
    for match in reversed(releases):
        words = words[:match.start()] + " " + words[match.end():]
    identity = normalize_title(words, keep_years=True)
    # A numeric-only alias embedded in a different movie's name can be
    # that movie's release year. Require it to be the visible title.
    usable = [
        name for name in usable
        if not YEAR_RE.fullmatch(name) or name == identity
        or (year and re.match(rf"^{name} {year}\b", original_words))
    ]
    return identity, year, usable


@dataclass(frozen=True)
class MatchResult:
    tier: MatchTier
    score: float
    evidence: dict
    reason: str | None = None


def classify_candidate(
    film: Film,
    candidate_title: str,
    *,
    duration_sec: int | None = None,
) -> MatchResult:
    if EPISODE_RE.search(candidate_title):
        return MatchResult(MatchTier.REJECT, 0.0, {}, "tv_episode")

    normalized_candidate, candidate_year, aliases = title_and_release_year(candidate_title, film.titles)
    if not normalized_candidate or not aliases:
        return MatchResult(MatchTier.REJECT, 0.0, {}, "title_mismatch")
    scores = [difflib.SequenceMatcher(None, normalized_candidate, alias).ratio() for alias in aliases]
    contains = any(
        len(alias) >= 4 and alias in normalized_candidate
        for alias in aliases
    )
    score = max(scores)
    if contains:
        score = max(score, 0.9)

    if film.year and candidate_year and abs(film.year - candidate_year) > YEAR_TOLERANCE:
        return MatchResult(
            MatchTier.REJECT,
            score,
            {"film_year": film.year, "candidate_year": candidate_year},
            "wrong_year",
        )

    # A missing year must not let a sequel/subtitle masquerade as a one-word
    # film merely because its runtime happens to match.  For example,
    # "Avengers - Age of Ultron" is not "Avengers" (2012).
    if candidate_year is None and normalized_candidate not in aliases:
        for normalized_alias in aliases:
            if len(normalized_alias.split()) != 1:
                continue
            remaining = re.sub(
                rf"\b{re.escape(normalized_alias)}\b",
                " ",
                normalized_candidate,
                count=1,
            )
            meaningful_extra = [
                token for token in remaining.split() if not token.isdigit()
            ]
            if len(meaningful_extra) >= 2:
                return MatchResult(
                    MatchTier.REJECT,
                    score,
                    {
                        "film_year": film.year,
                        "candidate_year": candidate_year,
                        "unexpected_title_tokens": meaningful_extra,
                    },
                    "unexpected_title_extension",
                )

    runtime_delta = None
    alternate_runtime = None
    if film.runtime_min and duration_sec:
        runtime_ratio = duration_sec / 60 / film.runtime_min
        runtime_delta = abs(runtime_ratio - 1)
        if contains and candidate_year == film.year:
            documented = RELEASE_RUNTIMES.get((film.cr_film_id, film.year))
            if documented:
                alternate_ratio = duration_sec / 60 / documented
                alternate_delta = abs(alternate_ratio - 1)
                if alternate_delta <= RUNTIME_TOLERANCE and alternate_delta < runtime_delta:
                    runtime_ratio, runtime_delta = alternate_ratio, alternate_delta
                    alternate_runtime = documented
        if (
            runtime_ratio <= 1 - RUNTIME_SHORTFALL_REJECT
            or runtime_delta >= RUNTIME_HARD_REJECT
        ):
            return MatchResult(
                MatchTier.REJECT,
                score,
                {"runtime_delta": round(runtime_delta, 3)},
                "wrong_runtime",
            )

    evidence = {
        "title_similarity": round(score, 3),
        "title_contains_alias": contains,
        "film_year": film.year,
        "candidate_year": candidate_year,
        "year_match": bool(
            film.year
            and candidate_year
            and abs(film.year - candidate_year) <= YEAR_TOLERANCE
        ),
        "film_runtime_min": film.runtime_min,
        "candidate_runtime_sec": duration_sec,
        "runtime_delta": round(runtime_delta, 3) if runtime_delta is not None else None,
    }
    if alternate_runtime is not None:
        evidence["documented_release_runtime_min"] = alternate_runtime
    if score < SIMILARITY_GATE and not contains:
        return MatchResult(MatchTier.REJECT, score, evidence, "title_mismatch")

    year_match = evidence["year_match"]
    runtime_match = runtime_delta is not None and runtime_delta <= RUNTIME_TOLERANCE
    if year_match and runtime_match:
        return MatchResult(MatchTier.STRONG, score, evidence)
    if year_match or runtime_match:
        return MatchResult(MatchTier.SOLID, score, evidence)
    return MatchResult(MatchTier.AMBIGUOUS, score, evidence, "title_only")
