"""
Phase 3 tests: renderer (color_for, render_segment, render_status_line, read_context_pct).
TDD: tests written before implementation.
"""
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


# ---------------------------------------------------------------------------
# color_for
# ---------------------------------------------------------------------------
class TestColorFor:
    def test_green_at_zero(self):
        cs = import_module()
        result = cs.color_for(0)
        assert '\033[38;5;82m' in result

    def test_green_at_49(self):
        cs = import_module()
        result = cs.color_for(49)
        assert '\033[38;5;82m' in result

    def test_yellow_at_50(self):
        cs = import_module()
        result = cs.color_for(50)
        assert '\033[38;5;220m' in result

    def test_yellow_at_74(self):
        cs = import_module()
        result = cs.color_for(74)
        assert '\033[38;5;220m' in result

    def test_orange_at_75(self):
        cs = import_module()
        result = cs.color_for(75)
        assert '\033[38;5;208m' in result

    def test_orange_at_89(self):
        cs = import_module()
        result = cs.color_for(89)
        assert '\033[38;5;208m' in result

    def test_red_at_90(self):
        cs = import_module()
        result = cs.color_for(90)
        assert '\033[38;5;196m' in result

    def test_red_at_100(self):
        cs = import_module()
        result = cs.color_for(100)
        assert '\033[38;5;196m' in result


# ---------------------------------------------------------------------------
# render_segment
# ---------------------------------------------------------------------------
class TestRenderSegment:
    def test_contains_label(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, 'resets today 14:40', use_color=False)
        assert 'Session' in out

    def test_contains_pct(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, 'resets today 14:40', use_color=False)
        assert '54%' in out

    def test_contains_note(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, 'resets today 14:40', use_color=False)
        assert 'resets today 14:40' in out

    def test_critical_warning_symbol(self):
        cs = import_module()
        out = cs.render_segment('Session', 95.0, 'resets today 14:40', use_color=False)
        assert '⚠' in out

    def test_no_warning_below_90(self):
        cs = import_module()
        out = cs.render_segment('Session', 89.0, use_color=False)
        assert '⚠' not in out

    def test_no_color_no_ansi(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, use_color=False)
        assert '\033[' not in out

    def test_with_color_has_ansi(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, use_color=True)
        assert '\033[' in out

    def test_no_color_ascii_bar(self):
        cs = import_module()
        out = cs.render_segment('Session', 54.0, use_color=False)
        # ASCII bar style [------....]
        assert '[' in out and ']' in out

    def test_empty_note_no_crash(self):
        cs = import_module()
        out = cs.render_segment('Weekly', 6.0, use_color=False)
        assert 'Weekly' in out
        assert '6%' in out


# ---------------------------------------------------------------------------
# render_status_line
# ---------------------------------------------------------------------------
class TestRenderStatusLine:
    def _make_result(self, pct, resets_at='2026-06-13T07:40:00+00:00'):
        return {'pct': pct, 'resets_at': resets_at, 'is_estimate': False}

    def test_full_width_has_all_segments(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=False,
            term_width=120,
        )
        assert 'Session' in out
        assert 'Weekly' in out
        assert 'Context' in out

    def test_medium_width_no_context(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name=None,
            subscription=None,
            is_fallback=False,
            use_color=False,
            term_width=85,
        )
        assert 'Session' in out
        assert 'Weekly' in out
        assert 'Context' not in out

    def test_minimal_width_only_session_weekly(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=None,
            ctx_detail=None,
            model_name=None,
            subscription=None,
            is_fallback=False,
            use_color=False,
            term_width=70,
        )
        assert 'Session' in out
        assert 'Weekly' in out

    def test_model_name_formatted(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=None,
            ctx_detail=None,
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=False,
            term_width=120,
        )
        assert 'Sonnet 4.6' in out

    def test_subscription_capitalized(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=None,
            ctx_detail=None,
            model_name=None,
            subscription='max',
            is_fallback=False,
            use_color=False,
            term_width=120,
        )
        assert 'Max' in out

    def test_fallback_adds_est_suffix(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        five_hour['is_estimate'] = True
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        seven_day['is_estimate'] = True
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=None,
            ctx_detail=None,
            model_name=None,
            subscription=None,
            is_fallback=True,
            use_color=False,
            term_width=120,
        )
        assert 'est.' in out

    def test_no_color_no_ansi(self):
        cs = import_module()
        five_hour = self._make_result(54.0)
        seven_day = self._make_result(6.0, '2026-06-16T10:00:00+00:00')
        out = cs.render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=None,
            ctx_detail=None,
            model_name=None,
            subscription=None,
            is_fallback=False,
            use_color=False,
            term_width=120,
        )
        assert '\033[' not in out


# ---------------------------------------------------------------------------
# read_context_pct (integration with tmp files)
# ---------------------------------------------------------------------------
class TestReadContextPct:
    def test_returns_none_when_no_files(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)
        pct, detail = cs.read_context_pct()
        assert pct is None
        assert detail is None

    def test_parses_context_from_jsonl(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)

        # Create a JSONL with an assistant record that has input_tokens
        # context usage: input 160000 tokens out of 200000
        import json, time
        uuid_dir = tmp_path / 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        uuid_dir.mkdir()
        jsonl_file = uuid_dir / 'session.jsonl'
        record = {
            "type": "assistant",
            "message": {
                "id": "msg_ctx001",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 160000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 500
                }
            },
            "timestamp": "2026-06-13T05:00:00.000Z"
        }
        jsonl_file.write_text(json.dumps(record) + '\n')

        pct, detail = cs.read_context_pct()
        assert pct is not None
        assert pct == pytest.approx(80.0)  # 160000 / 200000
        assert '160K' in detail
        assert '200K' in detail

    def test_detail_small_total(self, tmp_path, monkeypatch):
        """When total < 1000, detail should show raw number."""
        cs = import_module()
        import claude_status as _cs
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)

        import json
        uuid_dir = tmp_path / 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        uuid_dir.mkdir()
        jsonl_file = uuid_dir / 'session.jsonl'
        record = {
            "type": "assistant",
            "message": {
                "id": "msg_ctx002",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 500,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 50
                }
            },
            "timestamp": "2026-06-13T05:00:00.000Z"
        }
        jsonl_file.write_text(json.dumps(record) + '\n')

        pct, detail = cs.read_context_pct()
        assert pct is not None
        # total = input_tokens = 500, ctx = 200000
        # detail: '500/200K'
        assert '500' in detail
        assert '200K' in detail
