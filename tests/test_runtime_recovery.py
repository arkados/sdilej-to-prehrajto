import importlib.util
from pathlib import Path

import pytest

from sdilej_to_prehrajto.cli import prepare_source_batch, recover_failed_sources
from sdilej_to_prehrajto.models import Film
from sdilej_to_prehrajto.ranking import SELECTION_POLICY
from sdilej_to_prehrajto.sources import SelectedSourceStore
from sdilej_to_prehrajto.state import StateStore


def test_runner_mirror_lists_and_deb822_are_normalized(tmp_path):
    spec = importlib.util.spec_from_file_location('mirrors', Path(__file__).parents[1] / 'scripts/configure_ubuntu_mirrors.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'sources.list.d').mkdir()
    (tmp_path / 'sources.list.d/ubuntu.sources').write_text('URIs: mirror+file:/etc/apt/apt-mirrors.txt\nSuites: noble noble-updates\n')
    (tmp_path / 'apt-mirrors.txt').write_text('http://azure.archive.ubuntu.com/ubuntu/\thttps=1\nhttp://archive.ubuntu.com/ubuntu/\n')
    (tmp_path / 'apt-mirrors-security.txt').write_text('http://security.ubuntu.com/ubuntu/\n')
    (tmp_path / 'sources.list').write_text('deb http://azure.archive.ubuntu.com/ubuntu noble main\n')
    (tmp_path / 'sources.list.d/vendor.list').write_text('deb https://vendor.example/repo stable main\n')
    assert module.configure(tmp_path) == 3
    assert module.configure(tmp_path) == 0
    assert 'azure.archive' not in (tmp_path / 'apt-mirrors.txt').read_text()
    assert 'https://archive.ubuntu.com/ubuntu/' in (tmp_path / 'apt-mirrors.txt').read_text()
    assert 'mirror+file:' in (tmp_path / 'sources.list.d/ubuntu.sources').read_text()
    assert (tmp_path / 'sources.list.d/vendor.list').read_text() == 'deb https://vendor.example/repo stable main\n'


@pytest.mark.parametrize('protected', [None, 'upload', 'prepared', 'previous_target_id', 'claim', 'historical_target', 'one_failure', 'other_error', 'new_source'])
def test_source_recovery_preserves_all_target_guards(tmp_path, protected):
    state = StateStore(tmp_path / 'sync.json')
    sources = SelectedSourceStore(tmp_path / 'sources.jsonl')
    sources.record({'cr_film_id': 1, 'source_id': '123', 'source_url': 'https://sdilej.cz/123/film.mkv', 'selection_policy': SELECTION_POLICY})
    attempts = [{'status': 'source_refresh_failed', 'source_id': '123', 'reason': 'Source detail has no authenticated download link; source_id=123'} for _ in range(3)]
    row = state.film(1)
    row['attempts'] = attempts
    if protected in {'upload', 'prepared', 'previous_target_id', 'claim'}:
        row[protected] = {'target_video_id': '777'}
    elif protected == 'historical_target':
        attempts[0]['target_video_id'] = '777'
    elif protected == 'one_failure':
        row['attempts'] = attempts[:1]
    elif protected == 'other_error':
        attempts[-1]['reason'] = 'Temporary network failure'
    elif protected == 'new_source':
        attempts[-1]['source_id'] = '456'
    original = state.snapshot(1)
    assert recover_failed_sources(state, sources) == (1 if protected is None else 0)
    assert state.snapshot(1) == original
    assert (sources.candidate(1) is None) == (protected is None)
    assert sources.get(1)['source_url'] == 'https://sdilej.cz/123/film.mkv'


@pytest.mark.parametrize("deep", [False, True])
def test_recovery_precedes_catalog_and_retries_are_fair(tmp_path, deep):
    state = StateStore(tmp_path / "scan.json")
    sources = SelectedSourceStore(tmp_path / "sources.jsonl")
    films = [Film(i, str(i), str(i), None, 2000, 90, "en") for i in range(1, 7)]
    for film_id, timestamp in [(1, "2026-10-01T16:00:00+00:00"), (2, "2026-10-01T15:00:00+00:00"), (6, "2026-10-01T17:00:00+00:00")]:
        state.film(film_id)["attempts"] = [{"attempted_at": timestamp}]
    sources.record({"cr_film_id": 6, "source_status": "rediscovery_needed"})
    calls = []

    class Pipeline:
        def __init__(self):
            self.state = state
            self.selected_sources = sources

        def prepare_sources(self, batch, limit, **kwargs):
            assert kwargs.get("deep_scan_only", False) == deep
            calls.append([film.cr_film_id for film in batch])
            return []

    prepare_source_batch([Pipeline(), Pipeline()], films, 4, max_scan=10,
                         deadline_monotonic=None, deep_scan_only=deep)
    # Recovery first; unseen ties preserve catalog rank; older retries first.
    assert sorted(calls) == sorted([[6, 4, 2], [3, 5, 1]])
    assert [film.cr_film_id for film in films] == list(range(1, 7))


def test_operator_review_reaches_deep_workers_ahead_of_the_old_backlog(tmp_path):
    state = StateStore(tmp_path / 'scan.json')
    sources = SelectedSourceStore(tmp_path / 'sources.jsonl')
    state.record_attempt(3, {'status': 'source_deep_scan_failed'})
    calls = []

    class Pipeline:
        def __init__(self):
            self.state, self.selected_sources = state, sources

        def prepare_sources(self, films, limit, **kwargs):
            calls.append(([film.cr_film_id for film in films], kwargs))
            return []

    films = [Film(i, str(i), str(i), None, 2000, 90, 'en') for i in range(1, 5)]
    prepare_source_batch([Pipeline()], films, 4, max_scan=4,
                         deadline_monotonic=None, deep_scan_only=True,
                         priority_film_ids={3})
    assert calls[0][0] == [3, 1, 2, 4]
    assert calls[0][1]['review_film_ids'] == {3}


@pytest.mark.parametrize('quality_ok', [False, True])
def test_explicit_review_keeps_completion_and_quality_guards(tmp_path, quality_ok):
    from sdilej_to_prehrajto.models import Candidate, LanguageTier, MatchTier
    from sdilej_to_prehrajto.pipeline import SyncPipeline
    from sdilej_to_prehrajto.subtitles import SubtitleQueue

    state = StateStore(tmp_path / 'scan.json')
    state.record_success(1, {'target_video_id': '777'})
    for film_id in (2, 3):
        state.record_attempt(film_id, {'status': 'source_deep_scan_failed'})
    sources = SelectedSourceStore(tmp_path / 'sources.jsonl')
    candidate = Candidate('10', 'https://sdilej.cz/10/film.mkv', 'Film',
                          size_bytes=3_000_000_000 if quality_ok else 100,
                          duration_sec=6000, width=1920, height=1080,
                          language_tier=LanguageTier.CZECH_AUDIO,
                          match_tier=MatchTier.STRONG)
    calls = []

    class Provider:
        def discover(self, film):
            calls.append(film.cr_film_id)
            return [candidate]

    pipeline = SyncPipeline(source_provider=Provider(), source_session=None,
                            target_session=None, state=state,
                            subtitle_queue=SubtitleQueue(tmp_path / 'subs.jsonl'),
                            selected_sources=sources)
    films = [Film(i, str(i), 'Film', None, 2000, 100, 'en') for i in (1, 2, 3)]
    result = pipeline.prepare_sources(films, 3, deep_scan_only=True, review_film_ids={1, 2})
    assert calls == [2]
    assert len(result) == int(quality_ok)
    assert state.snapshot(1)['upload']['target_video_id'] == '777'
