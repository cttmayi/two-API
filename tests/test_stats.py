import json

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
    assert hourly[0]["latency_per_output_token_ms"] == 20.1
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


def test_snapshot_limits_hourly_usage_to_latest_24_items(tmp_path):
    usage_path = tmp_path / "usage.json"
    usage_path.write_text(json.dumps({
        "hourly": [
            {
                "hour": f"2026-06-06 {hour:02d}:00",
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
    assert hourly[-1]["hour"] == "2026-06-06 29:00"


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
