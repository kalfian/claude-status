"""
Phase 1 tests: API path (keychain reader, API fetcher, parser, reset label formatter).
TDD: tests written before implementation.
"""
import json
import sys
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

FIXTURES = Path(__file__).parent / 'fixtures'

# ---------------------------------------------------------------------------
# Helpers to import claude_status without executing main()
# ---------------------------------------------------------------------------
def import_module():
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import claude_status
    return claude_status


# ---------------------------------------------------------------------------
# parse_api_result
# ---------------------------------------------------------------------------
class TestParseApiResult:
    def test_normal_response(self):
        cs = import_module()
        data = json.loads((FIXTURES / 'api_response_normal.json').read_text())
        five_hour, seven_day = cs.parse_api_result(data)

        assert five_hour['pct'] == pytest.approx(54.0)
        assert seven_day['pct'] == pytest.approx(6.0)
        assert five_hour['is_estimate'] is False
        assert seven_day['is_estimate'] is False
        assert '2026-06-13' in five_hour['resets_at']
        assert '2026-06-16' in seven_day['resets_at']

    def test_critical_response(self):
        cs = import_module()
        data = json.loads((FIXTURES / 'api_response_critical.json').read_text())
        five_hour, seven_day = cs.parse_api_result(data)

        assert five_hour['pct'] == pytest.approx(93.0)
        assert seven_day['pct'] == pytest.approx(87.0)
        assert five_hour['is_estimate'] is False
        assert seven_day['is_estimate'] is False

    def test_result_keys(self):
        cs = import_module()
        data = json.loads((FIXTURES / 'api_response_normal.json').read_text())
        five_hour, seven_day = cs.parse_api_result(data)
        for result in (five_hour, seven_day):
            assert 'pct' in result
            assert 'resets_at' in result
            assert 'is_estimate' in result


# ---------------------------------------------------------------------------
# format_reset_label
# ---------------------------------------------------------------------------
class TestFormatResetLabel:
    def _fixed_now(self):
        # Return a fixed "now" in UTC: 2026-06-13 05:00:00 UTC
        return datetime(2026, 6, 13, 5, 0, 0, tzinfo=timezone.utc)

    def test_same_day(self, monkeypatch):
        cs = import_module()
        # resets_at is 2h later on same local day
        resets_at = '2026-06-13T07:40:00.775696+00:00'

        # Monkeypatch datetime inside the module so now() returns fixed time
        import claude_status as _cs
        original_datetime = _cs.datetime

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 6, 13, 5, 0, 0, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 6, 13, 5, 0, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(_cs, 'datetime', MockDatetime)
        result = cs.format_reset_label(resets_at)
        assert 'resets today' in result
        assert ':' in result  # has time component HH:MM

    def test_different_day(self, monkeypatch):
        cs = import_module()
        resets_at = '2026-06-16T10:00:00.775719+00:00'

        import claude_status as _cs

        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 6, 13, 5, 0, 0, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 6, 13, 5, 0, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(_cs, 'datetime', MockDatetime)
        result = cs.format_reset_label(resets_at)
        assert 'resets' in result
        assert 'Jun 16' in result
        assert ':' in result

    def test_empty_string(self):
        cs = import_module()
        result = cs.format_reset_label('')
        assert result == ''

    def test_none_returns_empty(self):
        cs = import_module()
        result = cs.format_reset_label(None)
        assert result == ''


# ---------------------------------------------------------------------------
# read_keychain_token  (unit — mock subprocess)
# ---------------------------------------------------------------------------
class TestReadKeychainToken:
    def test_parses_valid_keychain_output(self, monkeypatch):
        cs = import_module()
        fixture_data = (FIXTURES / 'keychain_output.json').read_text()

        import subprocess

        class FakeResult:
            returncode = 0
            stdout = fixture_data

        monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
        result = cs.read_keychain_token()
        assert result is not None
        assert 'access_token' in result
        assert 'expires_at' in result
        assert 'subscription_type' in result
        assert result['subscription_type'] == 'pro'

    def test_returns_none_on_subprocess_failure(self, monkeypatch):
        cs = import_module()

        import subprocess

        def raise_exc(*a, **kw):
            raise subprocess.SubprocessError('not found')

        monkeypatch.setattr('subprocess.run', raise_exc)
        result = cs.read_keychain_token()
        assert result is None

    def test_returns_none_on_missing_key(self, monkeypatch):
        cs = import_module()
        bad_json = json.dumps({'wrong_key': {}})

        class FakeResult:
            returncode = 0
            stdout = bad_json

        monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
        result = cs.read_keychain_token()
        assert result is None

    def test_returns_none_on_nonzero_returncode(self, monkeypatch):
        cs = import_module()

        class FakeResult:
            returncode = 1
            stdout = ''

        monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
        result = cs.read_keychain_token()
        assert result is None
