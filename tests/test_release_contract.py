"""Release-level regression tests, independent of scenes/cloud credentials."""
import importlib.util
import math
from pathlib import Path

from vesper.config import HubConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_claims", ROOT / "scripts/verify_claims.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_scene_record_matrix():
    result = audit.scene_summary()
    assert result["recorded_rows"] == 90
    assert result["unique_scenes"] == 30
    assert set(result["rows_per_model"].values()) == {30}
    assert result["zero_infrastructure_failures_independently_verified"] is False


def test_recompute_existing_js_counts():
    result = audit.activity_summary()
    assert len(result["comparisons"]) == 5
    assert all(row["agrees_at_four_decimals"] for row in result["comparisons"].values())


def test_sensor_model_boundaries():
    result = audit.sensor_summary()
    assert result["first_observation_can_trigger_without_velocity_history"]
    assert not result["tracked_target_at_0_05_m_per_s_triggers"]
    assert result["tracked_target_at_0_20_m_per_s_triggers"]
    assert not result["target_behind_sensor_triggers"]
    assert math.isclose(result["low_over_high_nominal_range_ratio"], 7 / 12)


def test_hub_secrets_from_environment(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("SMARTTHINGS_TOKEN", "synthetic-test-token")
    assert HubConfig().ha_token == "synthetic-test-token"
    assert load_config(ROOT / "configs/default.yaml").hub.smartthings_token == "synthetic-test-token"
