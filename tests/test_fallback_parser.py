"""
Phase 2 tests: JSONL fallback parser.
TDD: tests written before implementation.
"""
import sys
from datetime import datetime, timezone, timedelta
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
# parse_session_requests
# ---------------------------------------------------------------------------
class TestParseSessionRequests:
    def test_dedupe_streaming_dups(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_streaming_dups.jsonl')
        records = cs.parse_session_requests([fp])

        # 5 records all with same message_id → only 1 deduplicated record
        assert len(records) == 1

    def test_dedupe_keeps_highest_output_tokens(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_streaming_dups.jsonl')
        records = cs.parse_session_requests([fp])

        # The last record has output_tokens=500, input_tokens=500
        # billable = 500 + 0 + 0*0.1 + 500 = 1000
        assert records[0]['billable'] == pytest.approx(1000.0)

    def test_skip_synthetic(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_synthetic.jsonl')
        records = cs.parse_session_requests([fp])

        # 5 lines: 3 real, 2 synthetic → 3 records
        assert len(records) == 3

    def test_synthetic_not_in_billable(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_synthetic.jsonl')
        records = cs.parse_session_requests([fp])

        models = [r['model'] for r in records]
        assert '<synthetic>' not in models

    def test_billable_formula(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_with_subagent.jsonl')
        records = cs.parse_session_requests([fp])

        # Record 1: input=1000, cache_creation=200, cache_read=100, output=300
        # billable = 1000 + 200 + 100*0.1 + 300 = 1510
        # Record 2: input=500, cache_creation=0, cache_read=50, output=150
        # billable = 500 + 0 + 50*0.1 + 150 = 655
        billables = sorted([r['billable'] for r in records])
        assert billables[0] == pytest.approx(655.0)
        assert billables[1] == pytest.approx(1510.0)

    def test_record_fields(self):
        cs = import_module()
        fp = str(FIXTURES / 'session_with_subagent.jsonl')
        records = cs.parse_session_requests([fp])

        for r in records:
            assert 'message_id' in r
            assert 'timestamp' in r
            assert 'billable' in r
            assert 'model' in r


# ---------------------------------------------------------------------------
# compute_window
# ---------------------------------------------------------------------------
class TestComputeWindow:
    def _make_requests(self):
        """Create 6 requests: 3 in the last 5h, 3 older."""
        now = datetime.now(timezone.utc)
        requests = []
        for i, hours_ago in enumerate([1, 2, 4, 6, 8, 10]):
            ts = (now - timedelta(hours=hours_ago)).isoformat()
            requests.append({
                'message_id': f'msg_{i:03d}',
                'timestamp': ts,
                'billable': 1000.0,
                'model': 'claude-sonnet-4-6',
            })
        return requests

    def test_filter_by_window(self):
        cs = import_module()
        requests = self._make_requests()
        result = cs.compute_window(requests, hours=5)
        # hours_ago 1, 2, 4 are inside 5h window
        assert result['request_count'] == 3

    def test_total_tokens_in_window(self):
        cs = import_module()
        requests = self._make_requests()
        result = cs.compute_window(requests, hours=5)
        assert result['total_tokens'] == pytest.approx(3000.0)

    def test_empty_requests(self):
        cs = import_module()
        result = cs.compute_window([], hours=5)
        assert result['total_tokens'] == 0.0
        assert result['request_count'] == 0
        assert result['oldest_ts'] is None

    def test_oldest_ts_present(self):
        cs = import_module()
        requests = self._make_requests()
        result = cs.compute_window(requests, hours=5)
        assert result['oldest_ts'] is not None

    def test_7day_window(self):
        cs = import_module()
        requests = self._make_requests()
        result = cs.compute_window(requests, hours=168)
        # All 6 requests are within last 10h, well inside 168h
        assert result['request_count'] == 6

    def test_full_return_structure(self):
        cs = import_module()
        result = cs.compute_window([], hours=5)
        assert 'total_tokens' in result
        assert 'request_count' in result
        assert 'oldest_ts' in result


# ---------------------------------------------------------------------------
# scan_session_files
# ---------------------------------------------------------------------------
class TestScanSessionFiles:
    def test_groups_by_uuid_directory(self, tmp_path, monkeypatch):
        cs = import_module()
        # Create a fake .claude/projects structure
        uuid1 = 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        uuid2 = 'aabbccdd-1234-4678-abcd-ef1234567890'
        (tmp_path / uuid1).mkdir(parents=True)
        (tmp_path / uuid2).mkdir(parents=True)
        (tmp_path / uuid1 / 'abc.jsonl').touch()
        (tmp_path / uuid1 / 'def.jsonl').touch()
        (tmp_path / uuid2 / 'ghi.jsonl').touch()

        # Monkeypatch the glob pattern base path
        import claude_status as _cs
        original_home = Path.home

        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)
        result = cs.scan_session_files()

        assert uuid1 in result
        assert uuid2 in result
        assert len(result[uuid1]) == 2
        assert len(result[uuid2]) == 1

    def test_non_uuid_directories_ignored(self, tmp_path, monkeypatch):
        cs = import_module()
        import claude_status as _cs
        # Non-UUID directory
        (tmp_path / 'not-a-uuid').mkdir()
        (tmp_path / 'not-a-uuid' / 'file.jsonl').touch()
        # UUID directory
        uuid1 = 'def2a8bb-672b-4ef9-97db-6c3f67fd1317'
        (tmp_path / uuid1).mkdir()
        (tmp_path / uuid1 / 'file.jsonl').touch()

        monkeypatch.setattr(_cs, '_PROJECTS_BASE', tmp_path)
        result = cs.scan_session_files()

        assert 'not-a-uuid' not in result
        assert uuid1 in result
