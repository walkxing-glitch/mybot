"""Tests for HeartbeatConfig loading."""

from mybot.config import Config


def test_heartbeat_config_defaults():
    cfg = Config.from_dict({})
    assert cfg.heartbeat.enabled is False
    assert cfg.heartbeat.interval_seconds == 1800
    assert cfg.heartbeat.mirror_interval_hours == 24
    assert cfg.heartbeat.scout_interval_hours == 168


def test_heartbeat_config_from_yaml():
    cfg = Config.from_dict({
        "heartbeat": {
            "enabled": True,
            "interval_seconds": 900,
            "mirror": {"interval_hours": 12, "enabled": True},
            "scout": {"interval_hours": 72, "enabled": False},
            "skill_forge": {"enabled": True},
        }
    })
    assert cfg.heartbeat.enabled is True
    assert cfg.heartbeat.interval_seconds == 900
    assert cfg.heartbeat.mirror_interval_hours == 12
    assert cfg.heartbeat.scout_interval_hours == 72
    assert cfg.heartbeat.mirror_enabled is True
    assert cfg.heartbeat.scout_enabled is False
    assert cfg.heartbeat.skill_forge_enabled is True
