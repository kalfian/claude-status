#!/usr/bin/env python3
"""
claude_status.py — Claude Code Stop hook that displays a real-time usage statusline.
stdlib only, always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CTX_WINDOW = {
    'claude-sonnet-4-6': 200_000,
    'claude-opus-4-6': 200_000,
    'claude-haiku-4-5-20251001': 200_000,
}
DEFAULT_CTX = 200_000

ANSI = {
    'green':  '\033[38;5;82m',
    'yellow': '\033[38;5;220m',
    'orange': '\033[38;5;208m',
    'red':    '\033[38;5;196m',
    'dim':    '\033[2m',
    'bold':   '\033[1m',
    'reset':  '\033[0m',
}

# Internal path constant used for scan_session_files (monkeypatched in tests)
_PROJECTS_BASE: Path = Path.home() / '.claude' / 'projects'

CONFIG_PATH: Path = Path.home() / '.claude' / 'claude-status-config.json'

DEFAULT_CONFIG = {
    'plan': 'pro',
    'custom_5h_limit': None,
    'custom_weekly_limit': None,
    'no_color': False,
    'quiet_below_pct': 0,
}

INSTALL_PATH = Path.home() / '.claude' / 'scripts' / 'claude_status.py'
SETTINGS_PATH = Path.home() / '.claude' / 'settings.json'

_UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Module: Keychain reader
# ---------------------------------------------------------------------------
def read_keychain_token() -> Optional[dict]:
    """Returns {'access_token': str, 'expires_at': int, 'subscription_type': str} or None."""
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        outer = json.loads(result.stdout.strip())
        oauth = outer['claudeAiOauth']
        return {
            'access_token': oauth['accessToken'],
            'expires_at': oauth['expiresAt'],
            'subscription_type': oauth.get('subscriptionType', 'pro'),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Module: API fetcher
# ---------------------------------------------------------------------------
def fetch_usage_api(access_token: str) -> Optional[dict]:
    """GET https://api.anthropic.com/api/oauth/usage; returns raw JSON dict or None."""
    url = 'https://api.anthropic.com/api/oauth/usage'
    req = Request(url, headers={
        'Authorization': f'Bearer {access_token}',
        'anthropic-beta': 'oauth-2025-04-20',
    })
    try:
        with urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Module: API parser
# ---------------------------------------------------------------------------
def parse_api_result(data: dict) -> Tuple[dict, dict]:
    """Returns (five_hour_result, seven_day_result).
    Each: {'pct': float, 'resets_at': str, 'is_estimate': False}
    """
    def _extract(key: str) -> dict:
        block = data.get(key, {})
        return {
            'pct': float(block.get('utilization', 0.0)),
            'resets_at': block.get('resets_at', ''),
            'is_estimate': False,
        }

    return _extract('five_hour'), _extract('seven_day')


# ---------------------------------------------------------------------------
# Module: Reset label formatter
# ---------------------------------------------------------------------------
def format_reset_label(resets_at_iso) -> str:
    """Convert ISO 8601 UTC to local time label.
    Same day → 'resets today HH:MM'
    Different day → 'resets Jun 14 HH:MM'
    Empty/None → ''
    """
    if not resets_at_iso:
        return ''
    try:
        # Python 3.7+ fromisoformat doesn't handle Z suffix
        iso = resets_at_iso.replace('Z', '+00:00')
        reset_dt = datetime.fromisoformat(iso)
        local_now = datetime.now(tz=reset_dt.tzinfo if reset_dt.tzinfo else timezone.utc)
        local_now_date = local_now.date()
        reset_local = reset_dt
        reset_date = reset_local.date()
        time_str = reset_local.strftime('%H:%M')
        if reset_date == local_now_date:
            return f'resets today {time_str}'
        else:
            month_day = reset_local.strftime('%b %-d')
            return f'resets {month_day} {time_str}'
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Module: Color selector
# ---------------------------------------------------------------------------
def color_for(pct: float) -> str:
    """0-49 → green, 50-74 → yellow, 75-89 → orange, >=90 → red.
    Returns the full ANSI color escape code string.
    """
    if pct >= 90:
        return ANSI['red']
    elif pct >= 75:
        return ANSI['orange']
    elif pct >= 50:
        return ANSI['yellow']
    else:
        return ANSI['green']


# ---------------------------------------------------------------------------
# Module: Segment renderer
# ---------------------------------------------------------------------------
def render_segment(
    label: str,
    pct: float,
    note: str = '',
    line_len: int = 10,
    use_color: bool = True,
) -> str:
    """Single segment: 'Session ────────── 54%  resets today 14:40'
    label: DIM, line: colored, pct: colored+BOLD, note: DIM
    >=90%: append ' ⚠' to note
    use_color=False: ASCII bar [------....] style
    """
    warn = pct >= 90

    if use_color:
        col = color_for(pct)
        reset = ANSI['reset']
        dim = ANSI['dim']
        bold = ANSI['bold']

        filled = round(pct / 100.0 * line_len)
        bar_chars = '─' * filled + '·' * (line_len - filled)
        bar = f'{col}{bar_chars}{reset}'
        pct_str = f'{col}{bold}{int(pct)}%{reset}'
        label_str = f'{dim}{label}{reset}'
        note_str = f'{dim}{note}{reset}' if note else ''
        if warn:
            note_str = f'{dim}{note} ⚠{reset}' if note else f'{dim}⚠{reset}'
    else:
        filled = round(pct / 100.0 * line_len)
        bar_chars = '-' * filled + '.' * (line_len - filled)
        bar = f'[{bar_chars}]'
        pct_str = f'{int(pct)}%'
        label_str = label
        note_str = note
        if warn:
            note_str = f'{note} ⚠' if note else '⚠'

    parts = [label_str, bar, pct_str]
    if note_str:
        parts.append(' ' + note_str)
    return ' '.join(parts[:3]) + (f'  {note_str}' if note_str else '')


# ---------------------------------------------------------------------------
# Module: Context reader
# ---------------------------------------------------------------------------
def read_context_pct() -> Tuple[Optional[float], Optional[str]]:
    """Read most recently modified JSONL file from _PROJECTS_BASE/**/*.jsonl.
    Find last assistant record, compute context %.
    Returns (pct: float, detail: str) or (None, None).
    """
    base = _PROJECTS_BASE
    if not base.exists():
        return None, None

    # Find all JSONL files and sort by mtime descending
    jsonl_files = []
    for p in base.rglob('*.jsonl'):
        try:
            jsonl_files.append((p.stat().st_mtime, p))
        except OSError:
            pass

    if not jsonl_files:
        return None, None

    jsonl_files.sort(key=lambda x: x[0], reverse=True)
    most_recent = jsonl_files[0][1]

    try:
        last_record = None
        with open(most_recent, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get('type') == 'assistant':
                        msg = obj.get('message', {})
                        usage = msg.get('usage', {})
                        if usage:
                            last_record = usage
                except json.JSONDecodeError:
                    pass

        if last_record is None:
            return None, None

        input_t = last_record.get('input_tokens', 0)
        cache_creation = last_record.get('cache_creation_input_tokens', 0)
        cache_read = last_record.get('cache_read_input_tokens', 0)
        output_t = last_record.get('output_tokens', 0)

        # Context window usage = input tokens (what was sent in context)
        # Cache tokens are part of input context as well
        # Output tokens are responses, not context consumption
        total = input_t + cache_creation + cache_read

        # Determine model from the same record
        model = None
        with open(most_recent, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get('type') == 'assistant':
                        msg = obj.get('message', {})
                        if msg.get('usage') == last_record:
                            model = msg.get('model')
                            break
                except json.JSONDecodeError:
                    pass

        ctx_window = CTX_WINDOW.get(model, DEFAULT_CTX) if model else DEFAULT_CTX
        pct = (total / ctx_window) * 100.0

        ctx_k = ctx_window // 1000
        if total < 1000:
            detail = f'{total}/{ctx_k}K'
        else:
            total_k = total // 1000
            detail = f'{total_k}K/{ctx_k}K'

        return pct, detail

    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Module: Full line renderer
# ---------------------------------------------------------------------------
def _format_model_name(model_name: str) -> str:
    """'claude-sonnet-4-6' → 'Sonnet 4.6'"""
    # Strip leading 'claude-'
    name = re.sub(r'^claude-', '', model_name, flags=re.IGNORECASE)
    # Extract variant and version: 'sonnet-4-6' → 'Sonnet 4.6'
    # Handle patterns like 'haiku-4-5-20251001' → 'Haiku 4.5'
    parts = name.split('-')
    if not parts:
        return model_name.title()
    variant = parts[0].capitalize()
    # Find version numbers (digits)
    version_parts = []
    for p in parts[1:]:
        if p.isdigit() and len(p) <= 2:
            version_parts.append(p)
        elif re.match(r'^\d{8}$', p):
            # Date suffix like 20251001 — skip
            break
        elif p.isdigit():
            break
    if version_parts:
        return f'{variant} {".".join(version_parts)}'
    return variant


def render_status_line(
    five_hour: dict,
    seven_day: dict,
    ctx_pct: Optional[float],
    ctx_detail: Optional[str],
    model_name: Optional[str],
    subscription: Optional[str],
    is_fallback: bool,
    use_color: bool,
    term_width: int,
) -> str:
    """Full line adaptive layout."""

    def _note(result: dict) -> str:
        base_note = format_reset_label(result.get('resets_at', ''))
        if is_fallback or result.get('is_estimate'):
            # Add est. suffix and ~ prefix to times
            if base_note:
                # replace 'resets' with 'resets ~' to prefix time
                base_note = base_note.replace('resets today ', 'resets ~today ')
                base_note = base_note.replace('resets ', '~resets ')
                return base_note + ' est.'
            return 'est.'
        return base_note

    session_seg = render_segment(
        'Session', five_hour['pct'], _note(five_hour),
        line_len=10, use_color=use_color,
    )
    weekly_seg = render_segment(
        'Weekly', seven_day['pct'], _note(seven_day),
        line_len=10, use_color=use_color,
    )

    sep = '   '

    # Right info (model + subscription)
    right_parts = []
    if model_name:
        right_parts.append(_format_model_name(model_name))
    if subscription:
        right_parts.append(subscription.capitalize())
    right_info = ' · '.join(right_parts) if right_parts else ''

    if term_width >= 110 and ctx_pct is not None:
        ctx_label = f'Context ({ctx_detail})' if ctx_detail else 'Context'
        ctx_seg = render_segment(
            ctx_label, ctx_pct, '', line_len=10, use_color=use_color,
        )
        line = sep.join([session_seg, weekly_seg, ctx_seg])
        if right_info:
            line = line + sep + right_info
    elif term_width >= 80:
        line = sep.join([session_seg, weekly_seg])
        if right_info:
            line = line + sep + right_info
    else:
        line = sep.join([session_seg, weekly_seg])

    return line


# ---------------------------------------------------------------------------
# Module: JSONL Fallback parser
# ---------------------------------------------------------------------------
def scan_session_files() -> dict:
    """Scan _PROJECTS_BASE/**/*.jsonl.
    Group by session UUID extracted from parent directory name.
    Return {session_uuid: [filepath, ...]}
    """
    base = _PROJECTS_BASE
    groups: dict = {}
    if not base.exists():
        return groups

    for p in base.rglob('*.jsonl'):
        parent_name = p.parent.name
        if _UUID4_RE.match(parent_name):
            groups.setdefault(parent_name, []).append(str(p))

    return groups


def parse_session_requests(filepaths: list) -> list:
    """Parse JSONL files, return deduplicated request records.
    Each record: {'message_id': str, 'timestamp': str, 'billable': float, 'model': str}
    Dedup: per message.id, keep record with highest output_tokens.
    Skip: message.model == '<synthetic>'.
    Skip: records missing usage data.
    """
    # message_id → best record dict (keyed by output_tokens for dedup)
    seen: dict = {}

    for fp in filepaths:
        try:
            with open(fp, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if obj.get('type') != 'assistant':
                        continue

                    msg = obj.get('message', {})
                    model = msg.get('model', '')
                    if model == '<synthetic>':
                        continue

                    usage = msg.get('usage')
                    if not usage:
                        continue

                    msg_id = msg.get('id', '')
                    input_t = usage.get('input_tokens', 0)
                    cache_creation = usage.get('cache_creation_input_tokens', 0)
                    cache_read = usage.get('cache_read_input_tokens', 0)
                    output_t = usage.get('output_tokens', 0)
                    billable = (
                        input_t
                        + cache_creation
                        + cache_read * 0.1
                        + output_t
                    )
                    timestamp = obj.get('timestamp', '')

                    record = {
                        'message_id': msg_id,
                        'timestamp': timestamp,
                        'billable': billable,
                        'model': model,
                        '_output_tokens': output_t,
                    }

                    if msg_id not in seen:
                        seen[msg_id] = record
                    else:
                        # Keep record with highest output_tokens
                        if output_t > seen[msg_id]['_output_tokens']:
                            seen[msg_id] = record

        except OSError:
            continue

    result = []
    for r in seen.values():
        result.append({k: v for k, v in r.items() if k != '_output_tokens'})
    return result


def compute_window(requests: list, hours: int) -> dict:
    """Filter requests by timestamp >= now - hours, sum billable tokens.
    Returns {'total_tokens': float, 'request_count': int, 'oldest_ts': str | None}
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    filtered = []
    for r in requests:
        ts_str = r.get('timestamp', '')
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            if ts >= cutoff:
                filtered.append(r)
        except ValueError:
            pass

    total_tokens = sum(r['billable'] for r in filtered)
    request_count = len(filtered)
    oldest_ts = None
    if filtered:
        oldest_ts = min(r['timestamp'] for r in filtered)

    return {
        'total_tokens': total_tokens,
        'request_count': request_count,
        'oldest_ts': oldest_ts,
    }


# ---------------------------------------------------------------------------
# Module: Config
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Load config from CONFIG_PATH, merge with defaults. Return merged dict."""
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text())
            config.update(data)
    except Exception:
        pass
    return config


# ---------------------------------------------------------------------------
# Module: Install/uninstall
# ---------------------------------------------------------------------------
def install():
    """Copy this script to INSTALL_PATH. Inject Stop hook into settings.json."""
    INSTALL_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(__file__, INSTALL_PATH)

    settings = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text())
            # Backup
            SETTINGS_PATH.with_suffix('.json.bak').write_text(
                json.dumps(settings, indent=2)
            )
        except Exception:
            pass

    hook_cmd = f'python3 {INSTALL_PATH}'
    hooks = settings.setdefault('hooks', {})
    stop_hooks = hooks.setdefault('Stop', [])

    # Idempotent: check if already present
    already = any(
        (isinstance(h, dict) and h.get('command', '') == hook_cmd)
        or h == hook_cmd
        for h in stop_hooks
    )
    if not already:
        stop_hooks.append({'command': hook_cmd, 'type': 'stop'})

    new_content = json.dumps(settings, indent=2)
    # Validate
    try:
        json.loads(new_content)
    except json.JSONDecodeError as e:
        print(f'Error: generated invalid JSON — {e}', file=sys.stderr)
        return

    SETTINGS_PATH.write_text(new_content)
    print(f'Installed claude_status hook → {INSTALL_PATH}')
    print(f'Updated settings.json with Stop hook.')


def uninstall():
    """Remove Stop hook from settings.json."""
    if not SETTINGS_PATH.exists():
        print('settings.json not found, nothing to do.')
        return

    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except Exception as e:
        print(f'Error reading settings.json: {e}', file=sys.stderr)
        return

    hook_cmd = f'python3 {INSTALL_PATH}'
    hooks = settings.get('hooks', {})
    stop_hooks = hooks.get('Stop', [])
    new_stop = [
        h for h in stop_hooks
        if not (
            (isinstance(h, dict) and h.get('command', '') == hook_cmd)
            or h == hook_cmd
        )
    ]
    hooks['Stop'] = new_stop
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
    print('Removed claude_status Stop hook from settings.json.')


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description='Claude Code usage statusline')
    parser.add_argument('--mode', choices=['status', 'json'], default='status')
    parser.add_argument('--set-plan', choices=['pro', 'max_100', 'custom'])
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--uninstall', action='store_true')
    args = parser.parse_args()

    if args.install:
        install()
        sys.exit(0)

    if args.uninstall:
        uninstall()
        sys.exit(0)

    if args.set_plan:
        cfg_data = {}
        try:
            if CONFIG_PATH.exists():
                cfg_data = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
        cfg_data['plan'] = args.set_plan
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg_data, indent=2))
        print(f'Plan set to {args.set_plan}')
        sys.exit(0)

    config = load_config()
    use_color = not config.get('no_color', False)

    five_hour = None
    seven_day = None
    subscription = None
    is_fallback = False

    # Try primary path: keychain → API
    token_info = read_keychain_token()
    if token_info:
        api_data = fetch_usage_api(token_info['access_token'])
        if api_data:
            five_hour, seven_day = parse_api_result(api_data)
            subscription = token_info.get('subscription_type')

    # Fallback: JSONL
    if five_hour is None or seven_day is None:
        is_fallback = True
        groups = scan_session_files()
        all_requests = []
        for fps in groups.values():
            all_requests.extend(parse_session_requests(fps))

        def _make_fallback_result(window: dict, hours: int) -> dict:
            total = window['total_tokens']
            # Use a rough estimate: pro = 5M tokens / 5h window
            # We don't know exact limit without API; mark is_estimate=True
            # Use a placeholder limit of 5_000_000 for 5h, 35_000_000 for 7d
            limit = 5_000_000 if hours <= 5 else 35_000_000
            pct = min((total / limit) * 100.0, 100.0)
            oldest_ts = window.get('oldest_ts', '')
            return {'pct': pct, 'resets_at': oldest_ts or '', 'is_estimate': True}

        five_window = compute_window(all_requests, hours=5)
        seven_window = compute_window(all_requests, hours=168)
        five_hour = _make_fallback_result(five_window, 5)
        seven_day = _make_fallback_result(seven_window, 168)

    # Read context
    ctx_pct, ctx_detail = read_context_pct()

    # Detect model from most recent JSONL
    model_name = None
    try:
        base = _PROJECTS_BASE
        if base.exists():
            jsonl_files = sorted(
                base.rglob('*.jsonl'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if jsonl_files:
                with open(jsonl_files[0], 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get('type') == 'assistant':
                                m = obj.get('message', {}).get('model')
                                if m and m != '<synthetic>':
                                    model_name = m
                        except json.JSONDecodeError:
                            pass
    except Exception:
        pass

    term_width = shutil.get_terminal_size((80, 24)).columns

    quiet_below = config.get('quiet_below_pct', 0)
    max_pct = max(five_hour['pct'], seven_day['pct'])
    if max_pct < quiet_below:
        sys.exit(0)

    if args.mode == 'json':
        output = {
            'five_hour': five_hour,
            'seven_day': seven_day,
            'ctx_pct': ctx_pct,
            'ctx_detail': ctx_detail,
            'model': model_name,
            'subscription': subscription,
            'is_fallback': is_fallback,
        }
        print(json.dumps(output))
    else:
        line = render_status_line(
            five_hour=five_hour,
            seven_day=seven_day,
            ctx_pct=ctx_pct,
            ctx_detail=ctx_detail,
            model_name=model_name,
            subscription=subscription,
            is_fallback=is_fallback,
            use_color=use_color,
            term_width=term_width,
        )
        print(line)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'claude_status error: {e}', file=sys.stderr)
    sys.exit(0)
