import pytest

from sdilej_to_prehrajto.matching import classify_candidate
from sdilej_to_prehrajto.models import Film, MatchTier


def film() -> Film:
    return Film(
        cr_film_id=1,
        slug="angelika-a-kral",
        title="Angelika a král",
        original_title="Angélique et le Roy",
        year=1966,
        runtime_min=100,
        original_language="fr",
    )


def test_accepts_numbered_release_with_matching_year_and_runtime() -> None:
    result = classify_candidate(
        film(),
        "Angelika 3 Angelika a král (1966) 4K h265 AC3 5.1 CZ.mkv",
        duration_sec=6005,
    )
    assert result.tier == MatchTier.STRONG


def test_rejects_different_angelika_film_by_year() -> None:
    result = classify_candidate(
        film(),
        "Angelika 4 Nezkrotná Angelika (1967) 4K CZ.mkv",
        duration_sec=4934,
    )
    assert result.tier == MatchTier.REJECT
    assert result.reason == "wrong_year"


def test_rejects_wrong_cut_even_when_title_and_year_match() -> None:
    result = classify_candidate(
        film(),
        "Angelika 3 Angelika a král (1966) 4K.mkv",
        duration_sec=3600,
    )
    assert result.tier == MatchTier.REJECT
    assert result.reason == "wrong_runtime"


def test_rejects_incomplete_release_shorter_by_more_than_quarter() -> None:
    result = classify_candidate(
        film(),
        "Angelika 3 Angelika a král (1966) 4K.mkv",
        duration_sec=3600 * 1 + 14 * 60,
    )
    assert result.tier == MatchTier.REJECT
    assert result.reason == "wrong_runtime"


def test_rejects_shorter_cut_even_when_difference_is_under_twenty_percent() -> None:
    result = classify_candidate(
        film(),
        "Angelika 3 Angelika a král (1966) 1080p.mkv",
        duration_sec=83 * 60,
    )
    assert result.tier == MatchTier.REJECT
    assert result.reason == "wrong_runtime"


@pytest.mark.parametrize(
    ("film_id", "title", "year", "catalog_minutes", "release_minutes", "source_seconds"),
    [
        (24064, "Synové hor", 1956, 92, 82, 4913),
        (21551, "Balada o vojákovi", 1959, 89, 85, 5054),
        (20410, "Oceán s Davidem Attenboroughem", 2025, 95, 84, 5036),
    ],
)
def test_documented_edition_keeps_title_year_and_duration_guards(
    film_id, title, year, catalog_minutes, release_minutes, source_seconds,
) -> None:
    entry = Film(film_id, "film", title, None, year, catalog_minutes, "cs")
    filename = f"{title} ({year}).mkv"
    documented = classify_candidate(entry, filename, duration_sec=source_seconds)
    assert documented.tier == MatchTier.STRONG
    assert documented.evidence["documented_release_runtime_min"] == release_minutes
    assert classify_candidate(entry, filename, duration_sec=catalog_minutes * 60).tier == MatchTier.STRONG
    assert classify_candidate(entry, filename, duration_sec=(release_minutes - 10) * 60).reason == "wrong_runtime"
    assert classify_candidate(entry, f"{title}.mkv", duration_sec=source_seconds).reason == "wrong_runtime"
    assert classify_candidate(entry, f"{title} ({year + 1}).mkv", duration_sec=source_seconds).reason == "wrong_year"


def test_accepts_longer_extended_cut_with_matching_title_and_year() -> None:
    result = classify_candidate(
        film(),
        "Angelika 3 Angelika a král (1966) Extended 4K.mkv",
        duration_sec=130 * 60,
    )
    assert result.tier == MatchTier.SOLID


def test_title_only_match_is_not_uploadable() -> None:
    result = classify_candidate(film(), "Angelika a král.mkv")
    assert result.tier == MatchTier.AMBIGUOUS


def test_rejects_sequel_subtitle_without_year_for_one_word_film() -> None:
    avengers = Film(
        cr_film_id=324,
        slug="avengers",
        title="Avengers",
        original_title="The Avengers",
        year=2012,
        runtime_min=143,
        original_language="en",
    )

    result = classify_candidate(
        avengers,
        "Avengers - Age of Ultron.mkv",
        duration_sec=8478,
    )

    assert result.tier == MatchTier.REJECT
    assert result.reason == "unexpected_title_extension"


def test_year_between_filename_underscores_is_recognized() -> None:
    trainspotting = Film(
        cr_film_id=230,
        slug="trainspotting",
        title="Trainspotting",
        original_title="Trainspotting",
        year=1996,
        runtime_min=94,
        original_language="en",
    )

    result = classify_candidate(
        trainspotting,
        "Trainspotting_1996_2160p_CZ_Dabing_h265.mkv",
        duration_sec=5640,
    )

    assert result.tier == MatchTier.STRONG
