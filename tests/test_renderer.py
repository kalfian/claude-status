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
    """Asserts the threshold → palette-slot mapping, not the palette values.

    The exact 256-color codes are a design decision and may be retuned; the
    tier boundaries are behaviour and must not move.
    """

    def test_green_at_zero(self):
        cs = import_module()
        assert cs.color_for(0) == cs.ANSI['green']

    def test_green_at_49(self):
        cs = import_module()
        assert cs.color_for(49) == cs.ANSI['green']

    def test_yellow_at_50(self):
        cs = import_module()
        assert cs.color_for(50) == cs.ANSI['yellow']

    def test_yellow_at_74(self):
        cs = import_module()
        assert cs.color_for(74) == cs.ANSI['yellow']

    def test_orange_at_75(self):
        cs = import_module()
        assert cs.color_for(75) == cs.ANSI['orange']

    def test_orange_at_89(self):
        cs = import_module()
        assert cs.color_for(89) == cs.ANSI['orange']

    def test_red_at_90(self):
        cs = import_module()
        assert cs.color_for(90) == cs.ANSI['red']

    def test_red_at_100(self):
        cs = import_module()
        assert cs.color_for(100) == cs.ANSI['red']

    def test_tiers_are_visually_distinct(self):
        cs = import_module()
        slots = [cs.ANSI[k] for k in ('green', 'yellow', 'orange', 'red')]
        assert len(set(slots)) == 4


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

    def test_nonzero_usage_fills_at_least_one_cell(self):
        """1% must not render as an empty gauge — 'some' is not 'none'."""
        cs = import_module()
        out = cs.render_segment('Session', 1.0, line_len=10, use_color=False)
        assert '[=---------]' in out

    def test_zero_usage_fills_nothing(self):
        cs = import_module()
        out = cs.render_segment('Session', 0.0, line_len=10, use_color=False)
        assert '[----------]' in out

    def test_full_usage_fills_every_cell(self):
        cs = import_module()
        out = cs.render_segment('Session', 100.0, line_len=10, use_color=False)
        assert '[==========]' in out

    def test_pct_column_width_is_stable(self):
        """Value block keeps a fixed width so the line doesn't jitter on refresh."""
        cs = import_module()
        widths = {
            cs._visible_len(cs.render_segment('Session', p, use_color=False))
            for p in (0.0, 7.0, 51.0, 89.0)  # >=90 intentionally adds ⚠
        }
        assert len(widths) == 1


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

    def test_git_branch_appended_to_right_info(self):
        cs = import_module()
        kwargs = dict(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=False,
            term_width=120,
        )
        with_branch = cs.render_status_line(git_branch='feature/x', **kwargs)
        assert '⎇ feature/x' in with_branch
        assert 'Pro · ⎇ feature/x' in with_branch
        # Omitted by default
        assert '⎇' not in cs.render_status_line(**kwargs)

    def test_cwd_appended_before_git_branch(self):
        cs = import_module()
        kwargs = dict(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=False,
            term_width=200,
        )
        out = cs.render_status_line(cwd='~/Developments/demo', git_branch='main', **kwargs)
        assert 'Pro · ~/Developments/demo · ⎇ main' in out
        # Omitted by default
        assert '~/Developments/demo' not in cs.render_status_line(**kwargs)

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

    def test_labels_abbreviate_below_60_columns(self):
        cs = import_module()
        kwargs = dict(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=None, ctx_detail=None, model_name=None, subscription=None,
            is_fallback=False, use_color=False,
        )
        tiny = cs.render_status_line(term_width=50, **kwargs)
        assert '5h' in tiny and '7d' in tiny
        assert 'Session' not in tiny

        normal = cs.render_status_line(term_width=60, **kwargs)
        assert 'Session' in normal and 'Weekly' in normal

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

    def test_never_exceeds_terminal_width(self):
        """A wrapped statusline is the worst failure mode — the fit ladder must
        keep every width tier, both render modes, inside the terminal."""
        cs = import_module()
        long_cwd = '/Users/someone/Developments/vibe-coding/a-rather-long-project-name'
        # 40 columns is the documented floor — below that two gauges plus their
        # values physically cannot fit.
        for width in (40, 48, 55, 59, 60, 72, 80, 89, 90, 95, 110, 119, 120,
                      130, 139, 140, 152, 200):
            for use_color in (True, False):
                for pct in (0.0, 51.0, 93.0, 100.0):
                    out = cs.render_status_line(
                        five_hour=self._make_result(pct),
                        seven_day=self._make_result(pct, '2026-06-16T10:00:00+00:00'),
                        ctx_pct=pct,
                        ctx_detail='156K/200K',
                        model_name='claude-sonnet-4-6',
                        subscription='max',
                        is_fallback=False,
                        use_color=use_color,
                        term_width=width,
                        git_branch='feature/some-branch',
                        cwd=long_cwd,
                    )
                    assert cs._visible_len(out) <= width, (
                        f'overflow at width={width} color={use_color} pct={pct}: '
                        f'{cs._visible_len(out)} cols'
                    )

    def test_model_and_branch_survive_narrow_widths(self):
        """The cwd yields under pressure; model/plan/branch never do."""
        cs = import_module()
        out = cs.render_status_line(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=True,
            term_width=110,
            git_branch='main',
            cwd='/Users/someone/Developments/vibe-coding/personal-bot',
        )
        assert 'Sonnet 4.6' in out
        assert 'Pro' in out
        assert '⎇ main' in out
        # Full path can't fit at 110 — it degrades to the folder name or drops.
        assert '/Users/someone/Developments' not in out

    def test_cwd_degrades_to_folder_name_before_being_dropped(self):
        cs = import_module()
        out = cs.render_status_line(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=True,
            term_width=152,
            git_branch='main',
            cwd='/Users/someone/Developments/vibe-coding/personal-bot',
        )
        assert 'personal-bot' in out
        assert '~/Developments/vibe-coding/personal-bot' not in out

    def test_single_class_divider(self):
        """· marks the metrics/identity boundary — same glyph as the identity
        cluster's own separators, so there's one separator style, not two."""
        cs = import_module()
        out = cs.render_status_line(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=True,
            term_width=200,
        )
        assert '│' not in out
        # boundary divider + 2 identity-cluster separators (model·plan, plan·branch-less here: model·plan)
        assert out.count('·') >= 2

        out_plain = cs.render_status_line(
            five_hour=self._make_result(54.0),
            seven_day=self._make_result(6.0, '2026-06-16T10:00:00+00:00'),
            ctx_pct=78.0,
            ctx_detail='156K/200K',
            model_name='claude-sonnet-4-6',
            subscription='pro',
            is_fallback=False,
            use_color=False,
            term_width=200,
        )
        assert '|' not in out_plain
        assert '  ·  ' in out_plain

    def test_identity_cluster_is_never_colored(self):
        """Color on this line means utilization; identity stays neutral."""
        cs = import_module()
        identity = cs._render_identity(
            'claude-sonnet-4-6', 'pro', '~/dev/app', 'main', True, 'full',
        )
        for slot in ('green', 'yellow', 'orange', 'red'):
            assert cs.ANSI[slot] not in identity


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

    def test_explicit_transcript_path_wins_over_newer_file(self, tmp_path, monkeypatch):
        """transcript_path must be used even if another session file is newer."""
        cs = import_module()
        import claude_status as _cs
        import json, os
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)

        def _record(input_tokens):
            return json.dumps({
                "type": "assistant",
                "message": {
                    "id": "msg_x",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 10,
                    },
                },
                "timestamp": "2026-06-13T05:00:00.000Z",
            }) + '\n'

        mine = tmp_path / 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        mine.mkdir()
        mine_file = mine / 'session.jsonl'
        mine_file.write_text(_record(20000))

        other = tmp_path / 'aaaaaaaa-672b-4ef9-97db-6c3f67fd1317'
        other.mkdir()
        other_file = other / 'session.jsonl'
        other_file.write_text(_record(180000))
        # Make the other session's file strictly newer
        os.utime(other_file, (2_000_000_000, 2_000_000_000))

        pct, _ = cs.read_context_pct(str(mine_file))
        assert pct == pytest.approx(10.0)  # 20000 / 200000

        # Without the path, the mtime fallback picks the wrong (newer) file
        pct_fallback, _ = cs.read_context_pct()
        assert pct_fallback == pytest.approx(90.0)

    def test_missing_transcript_path_falls_back_to_mtime(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        import json
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)

        uuid_dir = tmp_path / 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        uuid_dir.mkdir()
        (uuid_dir / 'session.jsonl').write_text(json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_y",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 10,
                },
            },
        }) + '\n')

        pct, _ = cs.read_context_pct(str(tmp_path / 'does-not-exist.jsonl'))
        assert pct == pytest.approx(50.0)

    def test_skips_sidechain_records(self, tmp_path, monkeypatch):
        """Subagent (isSidechain) turns must not override main context usage."""
        cs = import_module()
        import claude_status as _cs
        import json
        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)

        uuid_dir = tmp_path / 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        uuid_dir.mkdir()
        jsonl_file = uuid_dir / 'session.jsonl'

        def _record(input_tokens, sidechain):
            obj = {
                "type": "assistant",
                "message": {
                    "id": f"msg_{input_tokens}",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 10,
                    },
                },
            }
            if sidechain:
                obj["isSidechain"] = True
            return json.dumps(obj) + '\n'

        # Main turn first, then a subagent turn with much smaller usage
        jsonl_file.write_text(_record(120000, False) + _record(3000, True))

        pct, detail = cs.read_context_pct(str(jsonl_file))
        assert pct == pytest.approx(60.0)  # 120000 / 200000, not 3000
        assert '120K' in detail


# ---------------------------------------------------------------------------
# get_git_branch
# ---------------------------------------------------------------------------
class TestGetGitBranch:
    def test_returns_none_without_cwd(self):
        cs = import_module()
        assert cs.get_git_branch(None) is None
        assert cs.get_git_branch('') is None

    def test_returns_none_outside_repo(self, tmp_path):
        cs = import_module()
        assert cs.get_git_branch(str(tmp_path)) is None

    def test_returns_branch_name(self, tmp_path):
        cs = import_module()
        import os, subprocess
        env = dict(os.environ)
        env.update({
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.com',
            'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.com',
        })

        def _git(*args):
            return subprocess.run(['git', '-C', str(tmp_path), *args],
                                  capture_output=True, text=True, timeout=10,
                                  env=env, check=True)

        try:
            _git('init', '-b', 'feature/x')
            # rev-parse HEAD needs at least one commit (unborn HEAD fails)
            _git('commit', '--allow-empty', '-m', 'init')
        except Exception:
            pytest.skip('git unavailable')
        assert cs.get_git_branch(str(tmp_path)) == 'feature/x'


# ---------------------------------------------------------------------------
# _format_cwd
# ---------------------------------------------------------------------------
class TestFormatCwd:
    def test_returns_none_for_falsy(self):
        cs = import_module()
        assert cs._format_cwd(None) is None
        assert cs._format_cwd('') is None

    def test_shortens_home_prefix(self, monkeypatch):
        cs = import_module()
        monkeypatch.setenv('HOME', '/Users/tester')
        assert cs._format_cwd('/Users/tester/Developments/demo') == '~/Developments/demo'
        assert cs._format_cwd('/Users/tester') == '~'

    def test_keeps_path_outside_home(self, monkeypatch):
        cs = import_module()
        monkeypatch.setenv('HOME', '/Users/tester')
        assert cs._format_cwd('/tmp/work') == '/tmp/work'
        # Sibling dir sharing the prefix must not be mangled
        assert cs._format_cwd('/Users/tester2/x') == '/Users/tester2/x'
