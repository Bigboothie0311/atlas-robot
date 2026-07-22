import json
from pathlib import Path

import content_pipeline
import reel_package


def test_distribution_package_prepares_all_platforms_without_publishing(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"master-video")

    def cover(_video, out_path, _title):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"cover")
        return str(out_path)

    def variant(_video, out_path, hook):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(hook.encode())
        return str(out_path)

    monkeypatch.setattr(content_pipeline, "create_reel_cover", cover)
    monkeypatch.setattr(content_pipeline, "create_hook_variant", variant)
    plan = {
        "series": "Can a Pi Do This?",
        "title": "Pi camera test",
        "hook": "Can a Pi see this?",
        "hook_score": 91,
        "hook_candidates": [
            {"text": "Can a Pi see this?", "score": 91},
            {"text": "This Pi just learned to see.", "score": 85},
            {"text": "Watch the camera test.", "score": 70},
        ],
        "cta": "What should I test next?",
    }
    cues = [{"start": 0, "end": 2, "text": "Hello from Atlas."}]
    manifest = reel_package.create_distribution_package(
        master_video=source,
        package_directory=tmp_path / "package",
        plan=plan,
        caption="Caption #raspberrypi",
        cues=cues,
        translations={
            "es": {"caption": "Texto", "cues": ["Hola de Atlas."]}
        },
    )

    assert manifest["status"] == "prepared_not_published"
    assert manifest["external_actions_taken"] == []
    assert set(manifest["platform_exports"]) == set(reel_package.PLATFORMS)
    assert len(manifest["trial_variants"]) == 2
    assert (tmp_path / "package" / "cover.png").is_file()
    assert "Hola" in (
        tmp_path / "package" / "subtitles" / "es.srt"
    ).read_text()
    assert "#raspberrypi" in (
        tmp_path / "package" / "platforms" / "instagram" / "caption.txt"
    ).read_text()
    assert "#raspberrypi" in (
        tmp_path / "package" / "platforms" / "tiktok" / "caption.txt"
    ).read_text()
    assert len(content_pipeline.HASHTAG_PATTERN.findall((
        tmp_path
        / "package"
        / "platforms"
        / "youtube_shorts"
        / "description.txt"
    ).read_text())) == 30
    saved = json.loads((tmp_path / "package" / "manifest.json").read_text())
    assert saved["status"] == "prepared_not_published"
    assert saved["external_actions_taken"] == []


def test_brand_ass_contains_identity_subtitles_and_chapter_progress():
    ass = content_pipeline.build_brand_ass(
        duration_seconds=5,
        cues=[
            {"start": 0, "end": 2.5, "text": "First line"},
            {"start": 2.5, "end": 5, "text": "Second line"},
        ],
        title="Pi Test",
        series="Building Atlas",
    )
    assert "A.T.L.A.S. // RASPBERRY PI" in ass
    assert "First line" in ass
    assert "02/02" in ass
    assert "BUILDING ATLAS" in ass
