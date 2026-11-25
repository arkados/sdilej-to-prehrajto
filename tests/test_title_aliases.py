import pytest

from sdilej_to_prehrajto.title_aliases import enrich_titles, translated_titles


def test_export_reuses_movie_id_bound_names_without_network():
    old = {"tmdb_id": 3763, "title": "鬼婆", "search_titles": ["鬼婆", "Onibaba"]}
    fresh = dict(old, search_titles=["鬼婆"])
    other = dict(fresh, tmdb_id=999)
    assert enrich_titles([fresh, other], [old], None) == 1
    assert fresh == old
    assert other["search_titles"] == ["鬼婆"]


def test_lookup_accepts_only_titles_for_the_requested_movie():
    class Response:
        status_code = 200
        def json(self):
            return {"id": 123, "title": "鬼婆", "translations": {"translations": []},
                    "alternative_titles": {"titles": [{"title": "Onibaba"}]}}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    assert translated_titles(Session(), 123, "key") == ["Onibaba"]
    with pytest.raises(ValueError, match="identity"):
        translated_titles(Session(), 321, "key")
