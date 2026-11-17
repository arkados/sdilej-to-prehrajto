import gzip
import json

import pytest

from sdilej_to_prehrajto.migration import audit


def fixture(root):
    for directory in ("state", "config"):
        (root / directory).mkdir()
    baseline = {
        "films": {
            "1": {"target_ids": ["101"], "uploaded_target_id": "101"},
            "2": {"target_ids": ["202"]},
            "3": {"target_ids": ["303"]},
        },
        "verified_subtitles": {"101": {"checksum": "verified-proof"}},
    }
    with gzip.open(root / "config/migration-baseline.json.gz", "wt") as handle:
        json.dump(baseline, handle)
    rows = {"1": {"upload": {"target_video_id": "101"}},
            "2": {"prepared": {"target_video_id": "202"}},
            "3": {"previous_target_id": "303"}}
    subtitles = {"101": {"status": "attached_verified", "intent": {"checksum": "verified-proof"}}}
    (root / "state/subtitle-backfill.json").write_text(json.dumps({"videos": subtitles}))
    return rows


@pytest.mark.parametrize("lost_film", ["1", "2", "3"])
def test_audit_blocks_lost_completed_pending_and_historical_ids(tmp_path, lost_film):
    rows = fixture(tmp_path)
    rows.pop(lost_film)
    (tmp_path / "state/sync.json").write_text(json.dumps({"films": rows}))
    with pytest.raises(ValueError, match="lost target identity"):
        audit(tmp_path)


def test_audit_allows_reconciling_pending_and_adding_new_uploads(tmp_path):
    rows = fixture(tmp_path)
    rows["2"] = {"upload": {"target_video_id": "202"}}
    rows["4"] = {"upload": {"target_video_id": "404"}}
    (tmp_path / "state/sync.json").write_text(json.dumps({"films": rows}))
    assert audit(tmp_path)["completed_uploads"] == 3


def test_audit_blocks_lost_verified_subtitle_evidence(tmp_path):
    rows = fixture(tmp_path)
    (tmp_path / "state/sync.json").write_text(json.dumps({"films": rows}))
    (tmp_path / "state/subtitle-backfill.json").write_text('{"videos": {}}')
    with pytest.raises(ValueError, match="lost verified subtitle"):
        audit(tmp_path)
