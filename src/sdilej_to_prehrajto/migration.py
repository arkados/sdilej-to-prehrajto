"""Check migrated target identities before enabling production transfers."""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def target_ids(row: dict) -> set[str]:
    ids = {str(row[key]["target_video_id"])
           for key in ("upload", "prepared")
           if (row.get(key) or {}).get("target_video_id")}
    if row.get("previous_target_id"):
        ids.add(str(row["previous_target_id"]))
    ids.update(str(attempt["target_video_id"])
               for attempt in row.get("attempts", [])
               if attempt.get("target_video_id"))
    return ids


def audit(root: Path) -> dict[str, int]:
    with gzip.open(root / "config/migration-baseline.json.gz", "rt") as handle:
        baseline = json.load(handle)
    rows = json.loads((root / "state/sync.json").read_text())["films"]
    subtitles = json.loads((root / "state/subtitle-backfill.json").read_text())["videos"]
    for film_id, expected in baseline["films"].items():
        row = rows.get(film_id, {})
        if not set(expected["target_ids"]).issubset(target_ids(row)):
            raise ValueError(f"Migration lost target identity for film {film_id}")
        uploaded = expected.get("uploaded_target_id")
        if uploaded and str((row.get("upload") or {}).get("target_video_id")) != uploaded:
            raise ValueError(f"Migration changed completed target for film {film_id}")
    for video_id, intent in baseline["verified_subtitles"].items():
        row = subtitles.get(video_id, {})
        if row.get("status") != "attached_verified" or row.get("intent") != intent:
            raise ValueError(f"Migration lost verified subtitle for video {video_id}")
    return {
        "protected_films": len(baseline["films"]),
        "completed_uploads": sum(bool(row.get("upload")) for row in rows.values()),
        "verified_subtitles": len(baseline["verified_subtitles"]),
    }


if __name__ == "__main__":
    print(json.dumps(audit(Path.cwd()), sort_keys=True))
