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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CTX_WINDOW = {
    'claude-sonnet-4-6': 200_000,
    'claude-opus-4-6': 200_000,
    'claude-haiku-4-5-20251001': 200_000,
}
DEFAULT_CTX = 200_000

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
# Two information classes share this line and must not look alike:
#   metrics  (Session/Weekly/Context)  — gauges, carry the urgency ramp
#   identity (model/plan/project/branch) — neutral text, never colored
#
# Urgency ramp: hue AND brightness escalate with utilization, so "everything is
# fine" is visually quiet and only a real limit pulls the eye. All 256-color
# (not truecolor) for maximum terminal coverage.
#
# Neutral scale: 4 explicit gray steps instead of SGR 2 (faint), which several
# terminals silently drop — the hierarchy would collapse there.
ANSI = {
    'green':  '\033[38;5;71m',    # <50   calm sage
    'yellow': '\033[38;5;179m',   # 50-74 amber
    'orange': '\033[38;5;208m',   # 75-89 orange
    'red':    '\033[38;5;203m',   # >=90  alarm
    'value':  '\033[38;5;252m',   # brightest neutral — model name
    'label':  '\033[38;5;245m',   # segment labels, plan, branch
    'note':   '\033[38;5;243m',   # reset time, token detail
    'track':  '\033[38;5;238m',   # empty gauge track, separators (graphic, not text)
    'dim':    '\033[2m',          # kept for back-compat
    'bold':   '\033[1m',
    'reset':  '\033[0m',
}

# Gauge glyphs: heavy/light box-drawing rules join into one continuous track,
# so filled vs empty differs by weight *and* hue. Lighter than █/░, which read
# as a solid slab at statusline scale.
BAR_FILLED = '━'
BAR_EMPTY = '─'

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _visible_len(text: str) -> int:
    """Printable width of a string, ignoring ANSI escape sequences."""
    return len(_ANSI_RE.sub('', text))

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
_PLUGIN_MARKER = Path.home() / '.claude' / 'claude-status-plugin-root'
CLEANUP_SCRIPT_PATH = Path.home() / '.claude' / 'scripts' / 'claude-status-cleanup.py'
PLUGIN_KEY = 'claude-status@kalfian-claude-code'

# Disk cache for API responses — avoids hammering API on frequent statusLine refreshes
import tempfile as _tempfile
_CACHE_PATH = Path(_tempfile.gettempdir()) / 'claude-status-cache.json'
_CACHE_TTL_SECONDS = 60

_UUID4_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Module: Credential reader (cross-platform)
# ---------------------------------------------------------------------------
def _parse_credential_json(raw: str) -> Optional[dict]:
    """Parse OAuth JSON from any credential store and return normalized dict."""
    outer = json.loads(raw.strip())
    oauth = outer['claudeAiOauth']
    return {
        'access_token': oauth['accessToken'],
        'expires_at': oauth['expiresAt'],
        'subscription_type': oauth.get('subscriptionType', 'pro'),
    }


def _read_credentials_macos() -> Optional[dict]:
    """macOS: read from Keychain via `security` command."""
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        return _parse_credential_json(result.stdout)
    except Exception:
        return None


def _read_credentials_linux() -> Optional[dict]:
    """Linux: try secret-tool (libsecret / GNOME keyring)."""
    try:
        result = subprocess.run(
            ['secret-tool', 'lookup', 'service', 'Claude Code-credentials'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_credential_json(result.stdout)
    except Exception:
        pass
    return None


def _read_credentials_windows() -> Optional[dict]:
    """Windows: read from Credential Manager via PowerShell."""
    try:
        ps = (
            '$c = [System.Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory();'
            'Add-Type -AssemblyName System.Security;'
            '$cred = [System.Security.Cryptography.ProtectedData];'
            # Use built-in cmdkey to query, then read via .NET CredentialCache
            # Simpler: use PSCredential stored in Windows Credential Manager
            '$mgr = [Windows.Security.Credentials.PasswordVault,Windows.Security,'
            'ContentType=WindowsRuntime]::new();'
            '($mgr.FindAllByResource("Claude Code-credentials") | Select -First 1 |'
            '% { $_.RetrievePassword(); $_ }).Password'
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_credential_json(result.stdout)
    except Exception:
        pass
    return None


def read_keychain_token() -> Optional[dict]:
    """Returns {'access_token': str, 'expires_at': int, 'subscription_type': str} or None.
    Dispatches to the platform-appropriate credential store.
    """
    if sys.platform == 'darwin':
        return _read_credentials_macos()
    elif sys.platform == 'linux':
        return _read_credentials_linux()
    elif sys.platform == 'win32':
        return _read_credentials_windows()
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
# Module: Disk cache (avoids repeated API calls during frequent statusLine refreshes)
# ---------------------------------------------------------------------------
import time as _time_module


def _load_cache() -> Optional[dict]:
    """Return cached API data if still fresh (within TTL), else None."""
    try:
        raw = _CACHE_PATH.read_text()
        entry = json.loads(raw)
        age = _time_module.time() - entry.get('fetched_at', 0)
        if age < _CACHE_TTL_SECONDS:
            return entry.get('data')
    except Exception:
        pass
    return None


def _save_cache(data: dict) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps({'fetched_at': _time_module.time(), 'data': data}))
    except Exception:
        pass


def fetch_usage_cached(access_token: str) -> Optional[dict]:
    """Return API data from disk cache if fresh, else fetch + cache."""
    cached = _load_cache()
    if cached is not None:
        return cached
    data = fetch_usage_api(access_token)
    if data is not None:
        _save_cache(data)
    return data


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
# Cross-platform day formatting helper
# ---------------------------------------------------------------------------
def _fmt_day_no_pad(dt) -> str:
    """Format day number without leading zero. %-d on Unix, %#d on Windows."""
    fmt = '%#d' if sys.platform == 'win32' else '%-d'
    return dt.strftime(fmt)


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
        # Convert to device local timezone
        reset_local = reset_dt.astimezone()
        local_now = datetime.now().astimezone()
        time_str = reset_local.strftime('%H:%M')
        if reset_local.date() == local_now.date():
            return f'resets today {time_str}'
        else:
            month_day = f'{reset_local.strftime("%b")} {_fmt_day_no_pad(reset_local)}'
            return f'resets {month_day} {time_str}'
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Module: Compact reset label (color mode only)
# ---------------------------------------------------------------------------
def format_reset_compact(resets_at_iso) -> str:
    """Convert ISO 8601 UTC to compact local time label for color mode.
    Same day → '↺ HH:MM'
    Different day → '↺ Jun 16 HH:MM'
    Empty/None → ''
    """
    if not resets_at_iso:
        return ''
    try:
        iso = resets_at_iso.replace('Z', '+00:00')
        reset_dt = datetime.fromisoformat(iso)
        # Convert to device local timezone
        reset_local = reset_dt.astimezone()
        local_now = datetime.now().astimezone()
        time_str = reset_local.strftime('%H:%M')
        if reset_local.date() == local_now.date():
            return f'↺ {time_str}'
        else:
            return f'↺ {reset_local.strftime("%b")} {_fmt_day_no_pad(reset_local)} {time_str}'
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
    """Single gauge: 'Session ━━━━━─────  51% ↺ 13:30'

    Reading order is label → gauge → value → note (coarse to precise). The
    percentage is right-aligned to 3 columns so the number block never shifts
    width between refreshes — a 10s-refresh line that jitters is distracting.

    label: neutral gray, bar: urgency color over dark track, pct: color+bold,
    note: dimmer gray. >=90% inserts a ⚠ between value and note.
    use_color=False: '[=====-----]' plain-ASCII gauge, no escapes at all.
    """
    warn = pct >= 90

    # Any nonzero usage shows at least one filled cell — "some" must never
    # render as "none".
    if pct <= 0:
        filled = 0
    else:
        filled = min(line_len, max(1, round(pct / 100.0 * line_len)))
    empty = line_len - filled
    pct_str = f'{int(pct):>3}%'

    if use_color:
        col = color_for(pct)
        reset = ANSI['reset']
        bold = ANSI['bold']

        parts = [
            f'{ANSI["label"]}{label}{reset}',
            f'{col}{BAR_FILLED * filled}{ANSI["track"]}{BAR_EMPTY * empty}{reset}',
            f'{col}{bold}{pct_str}{reset}',
        ]
        if warn:
            parts.append(f'{col}{bold}⚠{reset}')
        if note:
            parts.append(f'{ANSI["note"]}{note}{reset}')
    else:
        parts = [label, f'[{"=" * filled}{"-" * empty}]', pct_str]
        if warn:
            parts.append('⚠')
        if note:
            parts.append(note)

    return ' '.join(parts)


# ---------------------------------------------------------------------------
# Module: Context reader
# ---------------------------------------------------------------------------
def _latest_transcript(transcript_path: Optional[str] = None) -> Optional[Path]:
    """Resolve the transcript JSONL to read.

    Prefers the explicit `transcript_path` sent by Claude Code on stdin. Falls
    back to the most recently modified JSONL under _PROJECTS_BASE (used for
    --dev/--debug or when stdin carries no payload).
    """
    if transcript_path:
        try:
            p = Path(transcript_path)
            if p.exists():
                return p
        except OSError:
            pass

    base = _PROJECTS_BASE
    if not base.exists():
        return None

    jsonl_files = []
    for p in base.rglob('*.jsonl'):
        try:
            jsonl_files.append((p.stat().st_mtime, p))
        except OSError:
            pass

    if not jsonl_files:
        return None

    jsonl_files.sort(key=lambda x: x[0], reverse=True)
    return jsonl_files[0][1]


def read_context_pct(transcript_path: Optional[str] = None) -> Tuple[Optional[float], Optional[str]]:
    """Read the session transcript JSONL, find last assistant record, compute context %.

    `transcript_path` is the path Claude Code sends on stdin; when omitted the
    most recently modified JSONL under _PROJECTS_BASE is used instead.
    Sidechain (subagent/Task) records are skipped — their usage is not the main
    conversation's context.
    Returns (pct: float, detail: str) or (None, None).
    """
    most_recent = _latest_transcript(transcript_path)
    if most_recent is None:
        return None, None

    try:
        last_record = None
        with open(most_recent, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get('isSidechain'):
                        continue
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
                    if obj.get('isSidechain'):
                        continue
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
# Module: Git branch
# ---------------------------------------------------------------------------
def get_git_branch(cwd: Optional[str]) -> Optional[str]:
    """Current git branch for `cwd`, or None if unavailable/detached HEAD."""
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=cwd, capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return None
        branch = (result.stdout or '').strip()
        if not branch or branch == 'HEAD':
            return None
        return branch
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Module: Full line renderer
# ---------------------------------------------------------------------------
def _format_cwd(cwd: Optional[str]) -> Optional[str]:
    """Compact display form of a working directory: home prefix → '~'."""
    if not cwd:
        return None
    try:
        home = os.path.expanduser('~')
        path = str(cwd)
        if home and home != os.sep:
            if path == home:
                return '~'
            if path.startswith(home + os.sep):
                return '~' + path[len(home):]
        return path
    except Exception:
        return None


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
    git_branch: Optional[str] = None,
    cwd: Optional[str] = None,
) -> str:
    """Full line adaptive layout."""

    def _note(result: dict) -> str:
        base_note = format_reset_compact(result.get('resets_at', '')) if use_color else format_reset_label(result.get('resets_at', ''))
        if is_fallback or result.get('is_estimate'):
            if not use_color and base_note:
                base_note = base_note.replace('resets today ', 'resets ~today ')
                base_note = base_note.replace('resets ', '~resets ')
            return (base_note + ' est.') if base_note else 'est.'
        return base_note

    # Gauge length scales with available space. Shorter bars at narrow widths
    # keep the metric group from crowding out project context. Below 60 columns
    # (a three-way tmux split) the labels abbreviate too — at that size the
    # gauge and the number are the message.
    if term_width >= 140:
        bar_len = 10
    elif term_width >= 120:
        bar_len = 8
    elif term_width >= 90:
        bar_len = 6
    elif term_width >= 60:
        bar_len = 5
    else:
        bar_len = 4
    # Floor for the gauge: 6 cells still reads as a proportion; below 90 columns
    # 4 is all there is room for.
    min_bar = 6 if term_width >= 90 else 4
    session_label, weekly_label, ctx_label = (
        ('Session', 'Weekly', 'Context') if term_width >= 60 else ('5h', '7d', 'Ctx')
    )

    show_context = term_width >= 110 and ctx_pct is not None
    show_identity = term_width >= 80

    # Metrics are grouped by whitespace only; the single · divider is reserved
    # for the boundary between the two information classes, so it reads as a
    # class boundary instead of being repeated three times. Same glyph as the
    # identity cluster's internal separators — one separator style, not two.
    metric_sep = '   '
    divider = f'  {ANSI["track"]}·{ANSI["reset"]}  ' if use_color else '  ·  '
    cwd_display = _format_cwd(cwd)

    def _assemble(bars: int, session_note: bool, weekly_note: bool,
                  ctx_note: bool, cwd_mode: str, with_identity: bool = True) -> str:
        segments = [
            render_segment(session_label, five_hour['pct'],
                           _note(five_hour) if session_note else '',
                           line_len=bars, use_color=use_color),
            render_segment(weekly_label, seven_day['pct'],
                           _note(seven_day) if weekly_note else '',
                           line_len=bars, use_color=use_color),
        ]
        if show_context:
            detail = ctx_detail if (ctx_note and use_color) else ''
            segments.append(render_segment(
                ctx_label, ctx_pct, detail, line_len=bars, use_color=use_color,
            ))
        line = metric_sep.join(segments)
        if not (show_identity and with_identity):
            return line
        identity = _render_identity(
            model_name, subscription, cwd_display, git_branch, use_color, cwd_mode,
        )
        return (line + divider + identity) if identity else line

    # Fit ladder — a wrapped statusline is the worst failure mode here, so the
    # order in which things yield is explicit rather than emergent:
    #   1. the full path shortens to the folder name
    #   2. the gauges shrink — a bar is a redundant encoding of the number
    #      beside it, so decoration yields before unique content
    #   3. the path drops
    #   4. context token count, then weekly reset date, then session reset time
    # Model, plan and branch are never dropped: short, bounded, and the whole
    # point of the cluster.
    mid_bar = max(min_bar, bar_len - 2)
    ladder = [
        (bar_len, True, True, True, 'full'),
        (bar_len, True, True, True, 'base'),
        (mid_bar, True, True, True, 'base'),
        (min_bar, True, True, True, 'base'),
        (min_bar, True, True, True, 'none'),
        (min_bar, True, True, False, 'none'),
        (min_bar, True, False, False, 'none'),
        (min_bar, False, False, False, 'none'),
    ]
    for cfg in ladder:
        line = _assemble(*cfg)
        if _visible_len(line) <= term_width:
            return line

    # Ladder exhausted (very narrow pane, or a very long branch name): the
    # metrics alone own the line now. Shrink the identity cluster into whatever
    # is left — a truncated branch, then model only, then nothing.
    metrics = _assemble(min_bar, False, False, False, 'none', with_identity=False)
    if not show_identity:
        return metrics
    budget = term_width - _visible_len(metrics) - _visible_len(divider)
    identity = _fit_identity(model_name, subscription, git_branch, use_color, budget)
    return (metrics + divider + identity) if identity else metrics


def _basename(path: str) -> str:
    """Last component of a display path ('~/dev/app' → 'app')."""
    stripped = path.rstrip('/')
    if not stripped:
        return path
    return stripped.rsplit('/', 1)[-1]


def _truncate(text: str, limit: int) -> str:
    """Tail-truncate with an ellipsis. Returns '' if `limit` is too small."""
    if limit <= 1:
        return ''
    if len(text) <= limit:
        return text
    return text[:limit - 1] + '…'


def _fit_identity(
    model_name: Optional[str],
    subscription: Optional[str],
    git_branch: Optional[str],
    use_color: bool,
    budget: int,
) -> str:
    """Last-resort identity cluster for a line that is already at its limit.

    Priority when there is almost no room: model → branch → plan. Each
    candidate is tried whole; the first one that fits wins, so the result is
    deterministic rather than a character-by-character squeeze.
    """
    if budget <= 0:
        return ''
    branch = f'⎇ {git_branch}' if git_branch else ''
    short_branch = _truncate(branch, 12) if branch else ''
    candidates = [
        (model_name, subscription, branch),
        (model_name, None, branch),
        (model_name, None, short_branch),
        (model_name, None, None),
        (None, None, branch),
    ]
    for model, plan, br in candidates:
        out = _render_identity(model, plan, None, None, use_color)
        if br:
            joiner = f' {ANSI["track"]}·{ANSI["reset"]} ' if use_color else ' · '
            br_txt = f'{ANSI["label"]}{br}{ANSI["reset"]}' if use_color else br
            out = (out + joiner + br_txt) if out else br_txt
        if out and _visible_len(out) <= budget:
            return out
    return ''


def _render_identity(
    model_name: Optional[str],
    subscription: Optional[str],
    cwd_display: Optional[str],
    git_branch: Optional[str],
    use_color: bool,
    cwd_mode: str = 'full',
) -> str:
    """Identity cluster: 'Sonnet 4.6 · Pro · ~/dev/app · ⎇ main'.

    Never colored — this class answers "who and where", not "how much"; colour
    on this line means utilization only. Hierarchy comes from the neutral
    scale: the two anchors (model, project) sit at 'value', their qualifiers
    (plan, branch) recede to 'label'.

    `cwd_mode` is driven by the caller's fit ladder: 'full' | 'base' | 'none'.
    """
    items = []  # (text, ansi token)
    if model_name:
        items.append((_format_model_name(model_name), 'value'))
    if subscription:
        items.append((subscription.capitalize(), 'label'))
    if cwd_display and cwd_mode != 'none':
        path = cwd_display if cwd_mode == 'full' else _basename(cwd_display)
        if path:
            items.append((path, 'value'))
    if git_branch:
        items.append((f'⎇ {git_branch}', 'label'))

    if not items:
        return ''
    if not use_color:
        return ' · '.join(t for t, _ in items)
    joiner = f' {ANSI["track"]}·{ANSI["reset"]} '
    return joiner.join(f'{ANSI[tok]}{t}{ANSI["reset"]}' for t, tok in items)


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

    hook_cmd = f'python3 "{INSTALL_PATH}"'
    hooks = settings.setdefault('hooks', {})
    stop_hooks = hooks.setdefault('Stop', [])

    # Idempotent: check if already present (match inner command string)
    already = any(
        isinstance(h, dict) and any(
            inner.get('command', '') == hook_cmd
            for inner in h.get('hooks', [])
            if isinstance(inner, dict)
        )
        for h in stop_hooks
    )
    if not already:
        stop_hooks.append({'hooks': [{'type': 'command', 'command': hook_cmd}]})

    # Register native statusLine widget (persistent bar below input text)
    existing_sl = settings.get('statusLine', {})
    if not existing_sl or existing_sl.get('command') != hook_cmd:
        settings['statusLine'] = {'type': 'command', 'command': hook_cmd}

    # Set refresh interval: 10s refresh + 60s API cache = nearly realtime without spam
    if 'refreshInterval' not in settings:
        settings['refreshInterval'] = 10000

    new_content = json.dumps(settings, indent=2)
    # Validate
    try:
        json.loads(new_content)
    except json.JSONDecodeError as e:
        print(f'Error: generated invalid JSON — {e}', file=sys.stderr)
        return

    tmp = SETTINGS_PATH.with_suffix('.json.tmp')
    tmp.write_text(new_content)
    tmp.replace(SETTINGS_PATH)
    print(f'Installed claude_status hook → {INSTALL_PATH}')
    print(f'Updated settings.json with Stop hook and statusLine widget.')


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

    hook_cmd = f'python3 "{INSTALL_PATH}"'
    hooks = settings.get('hooks', {})
    stop_hooks = hooks.get('Stop', [])
    new_stop = [
        h for h in stop_hooks
        if not (
            isinstance(h, dict) and any(
                inner.get('command', '') == hook_cmd
                for inner in h.get('hooks', [])
                if isinstance(inner, dict)
            )
        )
    ]
    hooks['Stop'] = new_stop

    # Remove statusLine if it points to our script
    if settings.get('statusLine', {}).get('command') == hook_cmd:
        del settings['statusLine']

    tmp = SETTINGS_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(SETTINGS_PATH)
    print('Removed claude_status Stop hook and statusLine from settings.json.')


# ---------------------------------------------------------------------------
# Module: Plugin lifecycle (called by hooks.json SessionStart / skill)
# ---------------------------------------------------------------------------

# Cleanup script written to ~/.claude/scripts/ on activate; auto-runs on SessionStart
# to remove statusLine when plugin is uninstalled/disabled.
_CLEANUP_SCRIPT = '''\
#!/usr/bin/env python3
"""claude-status auto-uninstall hook. Runs on SessionStart; removes statusLine when plugin disabled."""
from __future__ import annotations
import json
from pathlib import Path

SETTINGS = Path.home() / '.claude' / 'settings.json'
MARKER = Path.home() / '.claude' / 'claude-status-plugin-root'
PLUGIN_KEY = 'claude-status@kalfian-claude-code'
THIS_SCRIPT = str(Path.home() / '.claude' / 'scripts' / 'claude-status-cleanup.py')


def main() -> None:
    # Only act if plugin_activate() has run at least once (marker exists)
    if not MARKER.exists():
        return

    try:
        settings = json.loads(SETTINGS.read_text())
    except Exception:
        return

    # Guard: only clean up when enabledPlugins is explicitly present and plugin is not enabled.
    # If the key is absent entirely, settings.json predates the plugin system — don't touch.
    enabled_plugins = settings.get('enabledPlugins')
    if enabled_plugins is None:
        return
    if enabled_plugins.get(PLUGIN_KEY, False):
        return  # plugin is enabled — nothing to do

    changed = False

    # Remove statusLine only if it matches exactly what plugin_activate() set
    marker_script = MARKER.read_text().strip()
    expected_sl_cmd = f'python3 "{marker_script}"'
    if settings.get('statusLine', {}).get('command') == expected_sl_cmd:
        del settings['statusLine']
        changed = True

    # Remove ourselves from hooks.SessionStart
    hooks = settings.get('hooks', {})
    ss = hooks.get('SessionStart', [])
    new_ss = [
        h for h in ss
        if not any(
            THIS_SCRIPT in inner.get('command', '')
            for inner in h.get('hooks', [])
            if isinstance(inner, dict)
        )
    ]
    if len(new_ss) != len(ss):
        if new_ss:
            hooks['SessionStart'] = new_ss
        else:
            hooks.pop('SessionStart', None)
        changed = True

    if changed:
        tmp = SETTINGS.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(settings, indent=2))
        tmp.replace(SETTINGS)

    MARKER.unlink()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
'''


def _write_cleanup_script() -> None:
    CLEANUP_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLEANUP_SCRIPT_PATH.write_text(_CLEANUP_SCRIPT)
    CLEANUP_SCRIPT_PATH.chmod(0o755)


def plugin_activate() -> None:
    """Configure statusLine in settings.json and install cleanup hook. Called by SessionStart hook.

    Fast-path via marker file — settings.json only updated when plugin root changes.
    Always exits silently; must never block a session.
    """
    try:
        plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT', '')
        if not plugin_root:
            return

        script_path = str(Path(plugin_root) / 'claude_status.py')

        # Fast path: marker matches current plugin root → already fully configured
        if _PLUGIN_MARKER.exists() and _PLUGIN_MARKER.read_text().strip() == script_path:
            return

        status_cmd = f'python3 "{script_path}"'
        cleanup_cmd = f'python3 "{CLEANUP_SCRIPT_PATH}"'

        settings: dict = {}
        if SETTINGS_PATH.exists():
            try:
                settings = json.loads(SETTINGS_PATH.read_text())
            except Exception:
                return

        changed = False

        # Set statusLine
        if settings.get('statusLine', {}).get('command') != status_cmd:
            settings['statusLine'] = {'type': 'command', 'command': status_cmd}
            changed = True

        # Set refreshInterval
        if 'refreshInterval' not in settings:
            settings['refreshInterval'] = 10000
            changed = True

        # Register cleanup hook in settings.json SessionStart (persistent, survives uninstall)
        hooks = settings.setdefault('hooks', {})
        ss_hooks = hooks.setdefault('SessionStart', [])
        already = any(
            isinstance(h, dict) and any(
                cleanup_cmd in inner.get('command', '')
                for inner in h.get('hooks', [])
                if isinstance(inner, dict)
            )
            for h in ss_hooks
        )
        if not already:
            ss_hooks.append({'hooks': [{'type': 'command', 'command': cleanup_cmd, 'timeout': 3}]})
            changed = True

        if changed:
            new_content = json.dumps(settings, indent=2)
            json.loads(new_content)  # validate before writing
            tmp = SETTINGS_PATH.with_suffix('.json.tmp')
            tmp.write_text(new_content)
            tmp.replace(SETTINGS_PATH)

        _write_cleanup_script()
        _PLUGIN_MARKER.write_text(script_path)
    except Exception:
        pass  # Never fail — runs at session start


def plugin_deactivate() -> None:
    """Remove statusLine + cleanup hook from settings.json. Called via skill or manual cleanup."""
    try:
        plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT', '')
        if plugin_root:
            script_path = str(Path(plugin_root) / 'claude_status.py')
        elif _PLUGIN_MARKER.exists():
            script_path = _PLUGIN_MARKER.read_text().strip()
        else:
            print('claude-status: not configured as plugin (no marker file found).')
            return

        status_cmd = f'python3 "{script_path}"'
        cleanup_cmd = f'python3 "{CLEANUP_SCRIPT_PATH}"'

        if not SETTINGS_PATH.exists():
            print('settings.json not found, nothing to clean up.')
            return

        try:
            settings = json.loads(SETTINGS_PATH.read_text())
        except Exception as e:
            print(f'Error reading settings.json: {e}', file=sys.stderr)
            return

        changed = False

        if settings.get('statusLine', {}).get('command') == status_cmd:
            del settings['statusLine']
            changed = True
            print('claude-status: removed statusLine from settings.json.')
        else:
            print('claude-status: statusLine not found (already clean).')

        # Remove cleanup hook from settings.json SessionStart
        hooks = settings.get('hooks', {})
        ss = hooks.get('SessionStart', [])
        new_ss = [
            h for h in ss
            if not any(
                cleanup_cmd in inner.get('command', '')
                for inner in h.get('hooks', [])
                if isinstance(inner, dict)
            )
        ]
        if len(new_ss) != len(ss):
            if new_ss:
                hooks['SessionStart'] = new_ss
            else:
                hooks.pop('SessionStart', None)
            changed = True
            print('claude-status: removed cleanup hook from settings.json.')

        if changed:
            tmp = SETTINGS_PATH.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(settings, indent=2))
            tmp.replace(SETTINGS_PATH)

        if _PLUGIN_MARKER.exists():
            _PLUGIN_MARKER.unlink()
            print('claude-status: marker file removed.')
    except Exception as e:
        print(f'claude-status deactivate error: {e}', file=sys.stderr)


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
    parser.add_argument('--plugin-activate', action='store_true', help='Auto-configure statusLine in settings.json (called by UserPromptSubmit hook)')
    parser.add_argument('--plugin-deactivate', action='store_true', help='Remove plugin statusLine from settings.json (call before uninstalling)')
    parser.add_argument('--dev', action='store_true', help='Developer mode: verbose diagnostics to stderr')
    parser.add_argument('--debug', action='store_true', help='Write debug log to temp file (useful when running as hook)')
    args = parser.parse_args()

    if args.install:
        install()
        sys.exit(0)

    if args.uninstall:
        uninstall()
        sys.exit(0)

    if args.plugin_activate:
        plugin_activate()
        sys.exit(0)

    if args.plugin_deactivate:
        plugin_deactivate()
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
    dev = args.dev
    debug = args.debug

    # Collect diagnostic lines; flush to stderr and/or file at end
    _log_lines: list = []

    def _log(msg: str) -> None:
        if dev or debug:
            _log_lines.append(msg)

    five_hour = None
    seven_day = None
    subscription = None
    is_fallback = False

    import time as _time
    _start = _time.monotonic()

    _log(f'claude-status diagnostic  [{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}]')
    _log(f'  script      : {__file__}')
    _log(f'  python      : {sys.version.split()[0]}  platform={sys.platform}')
    _log(f'  plugin_root : {os.environ.get("CLAUDE_PLUGIN_ROOT", "(not set — running directly)")}')
    _log(f'  config      : {CONFIG_PATH}')
    _log(f'  plan        : {config.get("plan")}')

    # Native statusLine mode: Claude Code sends JSON via stdin with rate_limits, context_window, model
    native = None
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.buffer.read()
            if raw and raw.strip():
                native = json.loads(raw.decode('utf-8', errors='replace'))
        except Exception:
            pass

    _log(f'  native_input: {"yes" if native else "no (tty or empty)"}')

    # Extract rate limits from native JSON if available (avoids API call)
    if native:
        rl = native.get('rate_limits') or {}
        fh = rl.get('five_hour') or {}
        sd = rl.get('seven_day') or {}
        if fh.get('used_percentage') is not None:
            resets_fh = fh.get('resets_at')
            if isinstance(resets_fh, (int, float)):
                resets_fh = datetime.fromtimestamp(resets_fh, tz=timezone.utc).isoformat()
            five_hour = {'pct': float(fh['used_percentage']), 'resets_at': resets_fh or '', 'is_estimate': False}
        if sd.get('used_percentage') is not None:
            resets_sd = sd.get('resets_at')
            if isinstance(resets_sd, (int, float)):
                resets_sd = datetime.fromtimestamp(resets_sd, tz=timezone.utc).isoformat()
            seven_day = {'pct': float(sd['used_percentage']), 'resets_at': resets_sd or '', 'is_estimate': False}
        _log(f'  native_rl   : 5h={five_hour["pct"] if five_hour else "n/a"}% 7d={seven_day["pct"] if seven_day else "n/a"}%')

    # Try primary path: keychain → API (skipped if native already provided rate limits)
    if five_hour is None or seven_day is None:
        _log('--- primary path: Keychain + API ---')
        token_info = read_keychain_token()
        if token_info:
            _log(f'  keychain    : OK  subscription={token_info.get("subscription_type")}  expires_at={token_info.get("expires_at")}')
            t1 = _time.monotonic()
            cache_hit = _load_cache() is not None
            api_data = fetch_usage_cached(token_info['access_token'])
            elapsed_ms = int((_time.monotonic() - t1) * 1000)
            _log(f'  api fetch   : {"OK" if api_data else "FAIL"}  ({elapsed_ms}ms)  cache={"HIT" if cache_hit else "MISS"}')
            if api_data:
                _log(f'  raw api     : {json.dumps(api_data)}')
                five_hour, seven_day = parse_api_result(api_data)
                subscription = token_info.get('subscription_type')
        else:
            _log('  keychain    : FAIL (not found or access denied)')
    else:
        # Still read keychain for subscription type (fast, no network)
        token_info = read_keychain_token()
        if token_info:
            subscription = token_info.get('subscription_type')

    # Fallback: JSONL
    if five_hour is None or seven_day is None:
        is_fallback = True
        _log('--- fallback path: JSONL ---')
        groups = scan_session_files()
        _log(f'  sessions     : {len(groups)}')
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

    _log(f'  5h          : pct={five_hour["pct"]:.1f}%  resets_at={five_hour["resets_at"]}  est={five_hour["is_estimate"]}')
    _log(f'  7d          : pct={seven_day["pct"]:.1f}%  resets_at={seven_day["resets_at"]}  est={seven_day["is_estimate"]}')

    # Context window: use native JSON if available (already computed by Claude Code)
    ctx_pct, ctx_detail = None, None
    if native:
        cw = native.get('context_window') or {}
        used_pct = cw.get('used_percentage')
        if used_pct is not None:
            ctx_pct = float(used_pct)
            total = int(cw.get('context_window_size') or DEFAULT_CTX)
            used_tokens = int(total * used_pct / 100)
            ctx_detail = f'{used_tokens // 1000}K/{total // 1000}K'
    if ctx_pct is None:
        ctx_pct, ctx_detail = read_context_pct(native.get('transcript_path') if native else None)

    _log(f'  context     : {ctx_pct:.1f}% ({ctx_detail})' if ctx_pct is not None else '  context     : (not available)')

    # Model: use native JSON if available, otherwise scan JSONL
    model_name = None
    if native:
        m = (native.get('model') or {})
        model_name = m.get('id') or m.get('display_name') or None
    if not model_name:
        try:
            transcript = _latest_transcript(native.get('transcript_path') if native else None)
            if transcript is not None:
                with open(transcript, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if obj.get('isSidechain'):
                                continue
                            if obj.get('type') == 'assistant':
                                m = obj.get('message', {}).get('model')
                                if m and m != '<synthetic>':
                                    model_name = m
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass

    _log(f'  model       : {model_name or "(not detected)"}')

    # Git branch of the session's working directory
    if native:
        git_cwd = native.get('cwd') or (native.get('workspace') or {}).get('current_dir') or os.getcwd()
    else:
        git_cwd = os.getcwd()
    git_branch = get_git_branch(git_cwd)
    cwd_display = _format_cwd(git_cwd)

    _log(f'  cwd         : {cwd_display or "(none)"}')
    _log(f'  git_branch  : {git_branch or "(none)"}')

    term_width = shutil.get_terminal_size((80, 24)).columns
    _log(f'  term_width  : {term_width}  is_fallback={is_fallback}')
    _log(f'  elapsed_ms  : {int((_time.monotonic() - _start) * 1000)}')

    # Flush diagnostic log
    if _log_lines:
        log_text = '\n'.join(_log_lines) + '\n'
        if dev:
            print('\n' + log_text, file=sys.stderr, end='')
        if debug:
            log_path = Path(tempfile.gettempdir()) / 'claude-status-debug.log'
            try:
                with open(log_path, 'a') as f:
                    f.write(log_text + '\n')
                print(f'[claude-status] debug log → {log_path}', file=sys.stderr)
            except OSError:
                pass  # Never block the hook

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
            'cwd': cwd_display,
            'git_branch': git_branch,
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
            git_branch=git_branch,
            cwd=cwd_display,
        )
        print(line)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'claude_status error: {e}', file=sys.stderr)
    sys.exit(0)
