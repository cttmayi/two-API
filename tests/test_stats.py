import json
from datetime import datetime, timedelta

from src.stats import Stats, init_stats, get_stats


def test_snapshot_includes_empty_hourly_usage():
    stats = Stats()

    snapshot = stats.snapshot()

    assert snapshot["hourly"] == []


def test_record_aggregates_hourly_token_usage():
    stats = Stats()

    stats.record(
        "gpt-4o",
        "openai",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=101,
        cache_read_tokens=3,
        cache_write_tokens=2,
    )
    stats.record(
        "gpt-4o",
        "openai",
        prompt_tokens=7,
        completion_tokens=4,
        latency_ms=80,
        cache_read_tokens=None,
        cache_write_tokens=1,
    )

    hourly = stats.snapshot()["hourly"]

    assert len(hourly) == 1
    assert hourly[0]["requests"] == 2
    assert hourly[0]["prompt_tokens"] == 17
    assert hourly[0]["completion_tokens"] == 9
    assert hourly[0]["cache_read_tokens"] == 3
    assert hourly[0]["cache_write_tokens"] == 3
    assert hourly[0]["total_tokens"] == 26
    assert hourly[0]["total_latency_ms"] == 181
    assert hourly[0]["avg_latency_ms"] == 90.5
    assert hourly[0]["latency_per_output_token_ms"] == 49.7
    assert hourly[0]["hour"].endswith(":00")


def test_hourly_usage_is_loaded_from_file(tmp_path):
    usage_path = tmp_path / "usage.json"
    usage_path.write_text(json.dumps({
        "hourly": [
            {
                "hour": "2026-06-06 13:00",
                "requests": 2,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cache_read_tokens": 1,
                "cache_write_tokens": 0,
                "total_tokens": 15,
            }
        ]
    }))

    stats = Stats(str(usage_path))

    assert stats.snapshot()["hourly"] == [
        {
            "hour": "2026-06-06 13:00",
            "requests": 2,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_tokens": 1,
            "cache_write_tokens": 0,
            "total_tokens": 15,
            "total_latency_ms": 0,
            "models": {},
            "aliases": {},
            "avg_latency_ms": 0,
            "latency_per_output_token_ms": 0,
        }
    ]


def test_record_persists_hourly_usage_to_file(tmp_path):
    usage_path = tmp_path / "usage.json"
    stats = Stats(str(usage_path))

    stats.record("gpt-4o", "openai", 4, 6, 100, cache_read_tokens=2)
    reloaded = Stats(str(usage_path))

    hourly = reloaded.snapshot()["hourly"]
    assert len(hourly) == 1
    assert hourly[0]["requests"] == 1
    assert hourly[0]["prompt_tokens"] == 4
    assert hourly[0]["completion_tokens"] == 6
    assert hourly[0]["cache_read_tokens"] == 2
    assert hourly[0]["total_tokens"] == 10
    assert hourly[0]["aliases"][""]["model"] == "gpt-4o"
    assert hourly[0]["aliases"][""]["requests"] == 1


def test_snapshot_limits_hourly_usage_to_latest_24_items(tmp_path):
    usage_path = tmp_path / "usage.json"
    start = datetime(2026, 6, 6, 0, 0)
    usage_path.write_text(json.dumps({
        "hourly": [
            {
                "hour": (start + timedelta(hours=hour)).strftime("%Y-%m-%d %H:%M"),
                "requests": 1,
                "prompt_tokens": hour,
                "completion_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": hour,
            }
            for hour in range(30)
        ]
    }))

    hourly = Stats(str(usage_path)).snapshot()["hourly"]

    assert len(hourly) == 24
    assert hourly[0]["hour"] == "2026-06-06 06:00"
    assert hourly[-1]["hour"] == "2026-06-07 05:00"


def test_init_stats_configures_persistent_usage_file(tmp_path):
    usage_path = tmp_path / "usage.json"

    init_stats(str(usage_path))
    get_stats().record("gpt-4o", "openai", 1, 2, 100)

    assert usage_path.exists()


def test_hourly_usage_tracks_models_separately():
    stats = Stats()

    stats.record("gpt-4o", "openai", 10, 5, 100)
    stats.record("ark-deepseek-v4-flash", "openai", 7, 3, 90)
    stats.record("gpt-4o", "openai", 2, 1, 50)

    models = stats.snapshot()["hourly"][0]["models"]

    assert models["gpt-4o"]["requests"] == 2
    assert models["gpt-4o"]["prompt_tokens"] == 12
    assert models["gpt-4o"]["completion_tokens"] == 6
    assert models["gpt-4o"]["total_tokens"] == 18
    assert models["ark-deepseek-v4-flash"]["requests"] == 1
    assert models["ark-deepseek-v4-flash"]["prompt_tokens"] == 7
    assert models["ark-deepseek-v4-flash"]["completion_tokens"] == 3
    assert models["ark-deepseek-v4-flash"]["total_tokens"] == 10


def test_hourly_usage_tracks_aliases_separately_from_models():
    stats = Stats()

    stats.record("gpt-4o-mini", "openai", 10, 5, 100, alias="default")
    stats.record("gpt-4o-mini", "openai", 7, 4, 80, cache_read_tokens=2, alias="fast")

    hourly = stats.snapshot()["hourly"][0]

    assert hourly["models"]["gpt-4o-mini"]["requests"] == 2
    assert hourly["models"]["gpt-4o-mini"]["total_tokens"] == 26
    assert hourly["aliases"]["default"]["model"] == "gpt-4o-mini"
    assert hourly["aliases"]["default"]["requests"] == 1
    assert hourly["aliases"]["default"]["total_tokens"] == 15
    assert hourly["aliases"]["fast"]["model"] == "gpt-4o-mini"
    assert hourly["aliases"]["fast"]["requests"] == 1
    assert hourly["aliases"]["fast"]["total_tokens"] == 11
    assert hourly["aliases"]["fast"]["cache_read_tokens"] == 2


def test_hourly_usage_uses_empty_alias_for_non_alias_requests():
    stats = Stats()

    stats.record("gpt-4o", "openai", 1, 2, 50)

    aliases = stats.snapshot()["hourly"][0]["aliases"]

    assert aliases[""]["model"] == "gpt-4o"
    assert aliases[""]["requests"] == 1
    assert aliases[""]["total_tokens"] == 3


def test_record_detail_includes_alias_field():
    stats = Stats()

    stats.record_detail(
        model="gpt-4o-mini",
        alias="default",
        provider="openai",
        streaming=False,
        latency_ms=123,
        status=200,
        prompt_tokens=1,
        completion_tokens=2,
        cache_read=None,
        cache_write=None,
        input_messages=[],
        output_content={"content": "ok"},
    )

    recent = stats.snapshot()["recent"][0]

    assert recent["model"] == "gpt-4o-mini"
    assert recent["alias"] == "default"
