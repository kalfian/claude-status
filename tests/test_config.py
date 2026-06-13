"""
Phase 4 tests: config loading.
TDD: tests written before implementation.
"""
import json
import sys
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / 'fixtures'


def import_module():
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import claude_status
    return claude_status


class TestLoadConfig:
    def test_defaults_when_no_config_file(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        monkeypatch.setattr(_cs, 'CONFIG_PATH', tmp_path / 'nonexistent.json')
        config = cs.load_config()
        assert config['plan'] == 'pro'
        assert config['custom_5h_limit'] is None
        assert config['custom_weekly_limit'] is None
        assert config['no_color'] is False
        assert config['quiet_below_pct'] == 0

    def test_merge_partial_config(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        cfg_file = tmp_path / 'config.json'
        cfg_file.write_text(json.dumps({'plan': 'max_100'}))
        monkeypatch.setattr(_cs, 'CONFIG_PATH', cfg_file)
        config = cs.load_config()
        assert config['plan'] == 'max_100'
        # Remaining keys should still be defaults
        assert config['no_color'] is False
        assert config['quiet_below_pct'] == 0

    def test_merge_multiple_keys(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        cfg_file = tmp_path / 'config.json'
        cfg_file.write_text(json.dumps({'plan': 'max_100', 'no_color': True, 'quiet_below_pct': 10}))
        monkeypatch.setattr(_cs, 'CONFIG_PATH', cfg_file)
        config = cs.load_config()
        assert config['plan'] == 'max_100'
        assert config['no_color'] is True
        assert config['quiet_below_pct'] == 10
        assert config['custom_5h_limit'] is None

    def test_invalid_json_returns_defaults(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        cfg_file = tmp_path / 'config.json'
        cfg_file.write_text('NOT VALID JSON{{{')
        monkeypatch.setattr(_cs, 'CONFIG_PATH', cfg_file)
        config = cs.load_config()
        # Should fall back to defaults gracefully
        assert config['plan'] == 'pro'
