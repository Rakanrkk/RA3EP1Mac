"""Run only owned fullscreen/pointer/capture checks against the adaptive-viewport candidate.

The native planner build/compat-adaptive-viewport/display-plan is executed first; its
snapshot is strictly validated (raw Wine units, exclusive safe edges, drawable target,
raw/backing scales), and the probe runs with RA3_SAFE_VIEWPORT only. Renderer
drawables must equal the planned drawable target; the probe backbuffer may use the
native default independently. Captures match the exact owned probe Win32 pid only.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = ROOT / 'build/compat-adaptive-viewport'
FIXED_DIR = Path(__file__).resolve().parents[1] / 'fixed-viewport'
DISPLAY_RECOVERY = CANDIDATE / 'display-mode'
PLAN_SCHEMA = 'ra3-safe-viewport-plan-v1'
SPEC_ENV_NAME = 'RA3_SAFE_VIEWPORT'
RAW_SCALE_CONTRACT = 2  # Wine raw unit is opt-in Retina2 per the DisplayPlan contract
MAX_DIM = 32768
MIN_VIEWPORT = (320, 200)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PlanError(RuntimeError):
    """Raised by the pure planner-snapshot validator; no process or file effects."""


def _strict_int(value):
    """Return value when it is a JSON integer (bool is not an integer here)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _strict_number(value):
    """Return value when it is a JSON number (bool rejected); scales use this."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def parse_safe_viewport_spec(text):
    """Strict '<screenW>x<screenH>:<left>,<top>,<right>,<bottom>' reader.

    Mirrors the viewport_spec.h contract: unsigned bounded decimal fields only,
    exact separators, in-bounds edges, usable viewport. Returns a dict or None.
    """
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r'([0-9]{1,5})x([0-9]{1,5}):([0-9]{1,5}),([0-9]{1,5}),([0-9]{1,5}),([0-9]{1,5})',
                         text)
    if not match:
        return None
    screen_w, screen_h, left, top, right, bottom = (int(group) for group in match.groups())
    if max(screen_w, screen_h, left, top, right, bottom) > MAX_DIM:
        return None
    if screen_w <= 0 or screen_h <= 0:
        return None
    if right > screen_w or bottom > screen_h:
        return None
    if right - left < MIN_VIEWPORT[0] or bottom - top < MIN_VIEWPORT[1]:
        return None
    return {'screen_width': screen_w, 'screen_height': screen_h,
            'left': left, 'top': top, 'right': right, 'bottom': bottom,
            'viewport_width': right - left, 'viewport_height': bottom - top}


def validate_plan(plan):
    """Validate one planner snapshot dict; return the normalized plan or raise PlanError.

    Pure: no filesystem, process, or environment access. Requires exact JSON ints
    for geometry, a known environment specification string, in-bounds exclusive
    edges, consistent viewport extents, and a drawable target consistent with the
    raw viewport and the raw/backing scales.
    """
    if not isinstance(plan, dict):
        raise PlanError('Planner snapshot is not a JSON object')
    if plan.get('schema') != PLAN_SCHEMA:
        raise PlanError('Unknown planner schema: %r' % (plan.get('schema'),))
    environment = plan.get('environment')
    if not isinstance(environment, dict) or set(environment) != {SPEC_ENV_NAME}:
        raise PlanError('Planner environment must contain exactly %s' % SPEC_ENV_NAME)
    spec = parse_safe_viewport_spec(environment[SPEC_ENV_NAME])
    if spec is None:
        raise PlanError('Unknown %s format: %r' % (SPEC_ENV_NAME, environment[SPEC_ENV_NAME]))
    plan_out = dict(spec)
    plan_out['safe_viewport'] = environment[SPEC_ENV_NAME]
    int_fields = ('screen_width', 'screen_height', 'left', 'top', 'right', 'bottom',
                  'viewport_width', 'viewport_height', 'drawable_width', 'drawable_height',
                  'display_id')
    for name in int_fields:
        value = _strict_int(plan.get(name))
        if value is None:
            raise PlanError('Planner field %s is not a valid JSON int' % name)
        plan_out[name] = value
    if plan_out['screen_width'] <= 0 or plan_out['screen_height'] <= 0 \
            or plan_out['screen_width'] > MAX_DIM or plan_out['screen_height'] > MAX_DIM:
        raise PlanError('Planner screen size out of bounds')
    if plan_out['left'] < 0 or plan_out['top'] < 0 \
            or plan_out['right'] > plan_out['screen_width'] \
            or plan_out['bottom'] > plan_out['screen_height']:
        raise PlanError('Planner safe edges out of bounds')
    if plan_out['right'] - plan_out['left'] != plan_out['viewport_width'] \
            or plan_out['bottom'] - plan_out['top'] != plan_out['viewport_height']:
        raise PlanError('Planner viewport extent contradicts safe edges')
    for key in ('screen_width', 'screen_height', 'left', 'top', 'right', 'bottom'):
        if spec[key] != plan_out[key]:
            raise PlanError('%s disagrees with %s string' % (key, SPEC_ENV_NAME))
    if plan_out['viewport_width'] < MIN_VIEWPORT[0] or plan_out['viewport_height'] < MIN_VIEWPORT[1]:
        raise PlanError('Planner viewport below usable minimum')
    raw_scale = _strict_int(plan.get('raw_scale'))
    if raw_scale != RAW_SCALE_CONTRACT:
        raise PlanError('Planner raw_scale %r violates the raw-unit contract' % (plan.get('raw_scale'),))
    plan_out['raw_scale'] = raw_scale
    backing_scale = _strict_number(plan.get('backing_scale'))
    if backing_scale is None or not (0 < backing_scale <= 8):
        raise PlanError('Planner backing_scale %r is not a valid scale' % (plan.get('backing_scale'),))
    plan_out['backing_scale'] = backing_scale
    for axis in ('width', 'height'):
        derived = plan_out['viewport_' + axis] * backing_scale / raw_scale
        if abs(derived - plan_out['drawable_' + axis]) > 1.0:
            raise PlanError('drawable_%s %d contradicts raw viewport and scales (%.2f expected)'
                            % (axis, plan_out['drawable_' + axis], derived))
        if not (1 <= plan_out['drawable_' + axis] <= MAX_DIM):
            raise PlanError('drawable_%s out of bounds' % axis)
    display_id = plan_out['display_id']
    if not (0 <= display_id <= 0xFFFFFFFF):
        raise PlanError('Planner display_id out of range')
    return plan_out


def _fixture_plan(screen=(3200, 2000), edges=(0, 80, 3200, 2000), drawable=(3200, 1920),
                  raw_scale=2, backing=2):
    screen_w, screen_h = screen
    left, top, right, bottom = edges
    plan = {'schema': PLAN_SCHEMA, 'display_id': 1, 'raw_scale': raw_scale,
            'backing_scale': backing,
            'environment': {SPEC_ENV_NAME: '%dx%d:%d,%d,%d,%d' % (
                screen_w, screen_h, left, top, right, bottom)},
            'screen_width': screen_w, 'screen_height': screen_h,
            'left': left, 'top': top, 'right': right, 'bottom': bottom,
            'viewport_width': right - left, 'viewport_height': bottom - top,
            'drawable_width': drawable[0], 'drawable_height': drawable[1]}
    return plan


def self_test():
    """Pure fixture checks for the spec reader and planner validator; no side effects."""
    checks = []

    def check(name, condition):
        checks.append((name, bool(condition)))

    good = parse_safe_viewport_spec('3200x2000:0,80,3200,2000')
    check('spec.accepts', good == {'screen_width': 3200, 'screen_height': 2000,
                                   'left': 0, 'top': 80, 'right': 3200, 'bottom': 2000,
                                   'viewport_width': 3200, 'viewport_height': 1920})
    check('spec.rejects_capital_x', parse_safe_viewport_spec('3200X2000:0,80,3200,2000') is None)
    check('spec.rejects_extra_field', parse_safe_viewport_spec('3200x2000:0,80,3200,2000,5') is None)
    check('spec.rejects_sign', parse_safe_viewport_spec('3200x2000:-0,80,3200,2000') is None)
    check('spec.rejects_space', parse_safe_viewport_spec(' 3200x2000:0,80,3200,2000') is None)
    check('spec.rejects_oversize_field',
          parse_safe_viewport_spec('39999x2000:0,80,39999,2000') is None)
    check('spec.rejects_digit_run_overflow',
          parse_safe_viewport_spec('9' * 40 + 'x2000:0,80,1,1') is None)
    check('spec.rejects_tiny_viewport', parse_safe_viewport_spec('400x300:0,0,100,50') is None)
    check('spec.rejects_edge_out_of_screen',
          parse_safe_viewport_spec('400x300:0,0,401,300') is None)
    check('spec.rejects_non_string', parse_safe_viewport_spec(640) is None)
    check('spec.accepts_leading_zero',
          parse_safe_viewport_spec('0640x0400:0,0,0640,0400') is not None)

    check('plan.accepts_current_shape',
          validate_plan(_fixture_plan())['drawable_width'] == 3200)
    check('plan.accepts_integral_float_backing',
          validate_plan(_fixture_plan(backing=2.0))['backing_scale'] == 2.0)
    one_x = _fixture_plan(screen=(1920, 1080), edges=(0, 0, 1920, 1080),
                          drawable=(960, 540), backing=1)
    check('plan.accepts_1x_backing_halved_drawable',
          validate_plan(one_x)['drawable_height'] == 540)
    for name, mutate in (
        ('plan.rejects_bool_int', lambda p: p.update(screen_width=True)),
        ('plan.rejects_float_int', lambda p: p.update(viewport_width=1999.0)),
        ('plan.rejects_schema', lambda p: p.update(schema='other-v9')),
        ('plan.rejects_missing_env', lambda p: p.update(environment={})),
        ('plan.rejects_extra_env_key',
         lambda p: p.update(environment={**p['environment'], 'RA3_FIXED_VIEWPORT': '1x1:0,0,1,1'})),
        ('plan.rejects_env_mismatch', lambda p: p.update(screen_height=1999)),
        ('plan.rejects_extent_contradiction', lambda p: p.update(viewport_height=1899)),
        ('plan.rejects_edge_out_of_bounds', lambda p: p.update(bottom=2001)),
        ('plan.rejects_wrong_raw_scale', lambda p: p.update(raw_scale=3)),
        ('plan.rejects_bool_scale', lambda p: p.update(backing_scale=True)),
        ('plan.rejects_huge_scale', lambda p: p.update(backing_scale=64)),
        ('plan.rejects_drawable_contradiction',
         lambda p: p.update(drawable_width=2000, drawable_height=1250)),
        ('plan.rejects_bool_display_id', lambda p: p.update(display_id=False)),
        ('plan.rejects_negative_display_id', lambda p: p.update(display_id=-1)),
    ):
        plan = _fixture_plan()
        mutate(plan)
        try:
            validate_plan(plan)
            rejected = False
        except PlanError:
            rejected = True
        check(name, rejected)
    try:
        validate_plan('not-a-plan')
        check('plan.rejects_non_object', False)
    except PlanError:
        check('plan.rejects_non_object', True)
    failed = [name for name, ok in checks if not ok]
    if failed:
        raise RuntimeError('Self-test failures: %s' % ', '.join(failed))
    return len(checks)


def parse_probe_output(text):
    """Extract the owned probe's structured stdout fields (pure)."""
    record = {'pid': None, 'create_device': None, 'requested': None, 'screen': None,
              'window': None, 'client': None, 'monitor': None, 'backbuffer': None,
              'backbuffer_readback': None, 'pass': 'PASS:' in text}
    match = re.search(r'^pid=([0-9]+) create_device=([0-9a-f]{8}) requested=([0-9]+)x([0-9]+)\s*$',
                      text, re.MULTILINE)
    if match:
        record['pid'] = int(match[1])
        record['create_device'] = match[2]
        record['requested'] = [int(match[3]), int(match[4])]
    match = re.search(r'^screen=([0-9]+)x([0-9]+) window=(-?[0-9]+),(-?[0-9]+),(-?[0-9]+),(-?[0-9]+) '
                      r'client=([0-9]+)x([0-9]+) monitor=(-?[0-9]+),(-?[0-9]+),(-?[0-9]+),(-?[0-9]+) '
                      r'backbuffer=([0-9]+)x([0-9]+)\s*$', text, re.MULTILINE)
    if match:
        record['screen'] = [int(match[1]), int(match[2])]
        record['window'] = [int(match[3]), int(match[4]), int(match[5]), int(match[6])]
        record['client'] = [int(match[7]), int(match[8])]
        record['monitor'] = [int(match[9]), int(match[10]), int(match[11]), int(match[12])]
        record['backbuffer'] = [int(match[13]), int(match[14])]
    match = re.search(r'^backbuffer_top=([0-9a-f]{8}) bottom=([0-9a-f]{8}) readback=([0-9a-f]{8})\s*$',
                      text, re.MULTILINE)
    if match:
        record['backbuffer_readback'] = {'top': match[1], 'bottom': match[2],
                                         'readback_hresult': match[3]}
    return record


def renderer_drawables(payload):
    """Collect mtld3d present lines (guest raw vs drawable actual) from owned logs."""
    rows = []
    for path in sorted((payload / 'mtld3d-logs').glob('fullscreen_probe-*.log')):
        for match in re.finditer(r'present: guest ([0-9]+)x([0-9]+), layer [^\r\n]*?drawable ([0-9]+)x([0-9]+)',
                                 path.read_text(errors='replace')):
            rows.append({'guest': [int(match[1]), int(match[2])],
                         'drawable': [int(match[3]), int(match[4])],
                         'log': str(path)})
    return rows


def fullscreen_wine_windows(state):
    """Return onscreen wine windows that already cover ~a whole screen (pure)."""
    frames = [screen.get('frame', {}) for screen in state.get('screens', [])]
    hits = []
    for window in state.get('wineWindows', []):
        if not window.get('kCGWindowIsOnscreen'):
            continue
        bounds = window.get('kCGWindowBounds') or {}
        area = float(bounds.get('Width', 0)) * float(bounds.get('Height', 0))
        for frame in frames:
            full = float(frame.get('width', 0)) * float(frame.get('height', 0))
            if full > 0 and area >= 0.95 * full:
                hits.append(window)
                break
    return hits


def owned_windows_for_host_pids(state, host_pids):
    """Exact CGWindow match: onscreen wine windows owned by one of the host pids."""
    matches = []
    for window in state.get('wineWindows', []):
        owner = window.get('kCGWindowOwnerPID')
        if isinstance(owner, bool) or not isinstance(owner, (int, float)):
            continue
        if int(owner) in host_pids and window.get('kCGWindowIsOnscreen'):
            matches.append(window)
    return matches


def resolve_owned_host_pids(payload_name):
    """Resolve the owned probe's host pid from ps by exact Windows exe path.

    The probe's printed pid= is the Wine guest pid (GetCurrentProcessId) and does
    NOT match CGWindow kCGWindowOwnerPID, which is the native host pid. Host
    processes of this run are matched by the exact payload path plus probe
    basename appearing in their command arguments.
    """
    needle = 'C:\\' + payload_name + '\\fullscreen_probe.exe'
    listing = subprocess.check_output(['ps', '-axww', '-o', 'pid=,args='], text=True)
    pids = set()
    for line in listing.splitlines():
        pid_text, _, command = line.strip().partition(' ')
        if pid_text.isdigit() and needle in command:
            pids.add(int(pid_text))
    return sorted(pids)


def load_center_rows():
    """Load the fixed pixel analyzer from the fixed-viewport reference contract."""
    spec = importlib.util.spec_from_file_location('inspect_capture',
                                                  FIXED_DIR / 'inspect_capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.center_rows


def analyze_capture(png_path, plan):
    """Formula checks for one owned capture: RGBA8, top marker after notch, bottom marker."""
    center_rows = load_center_rows()
    pixels = center_rows(png_path)
    spans = pixels['center_column_spans']
    checks = {'png_rgba8': True, 'spans': spans,
              'capture_size': [pixels['width'], pixels['height']]}
    covers = (pixels['width'] >= plan['drawable_width'] - 2
              and pixels['height'] >= plan['drawable_height'] - 2)
    checks['capture_covers_drawable'] = covers
    raw_to_capture = plan['backing_scale'] / plan['raw_scale']
    checks['no_extra_overscan'] = (
        pixels['width'] <= plan['screen_width'] * raw_to_capture + 2
        and pixels['height'] <= plan['screen_height'] * raw_to_capture + 2)
    checks['limitation'] = ('Only center-column top/bottom marker rows are pixel-validated; '
                            'left/right inset columns are not analyzed.')
    labels = [span['label'] for span in spans]
    checks['green_marker_visible'] = 'green' in labels
    red_spans = [span for span in spans if span['label'] == 'red']
    checks['red_marker_visible'] = bool(red_spans)
    first_red = red_spans[0]['start_row'] if red_spans else None
    before = [span['label'] for span in spans
              if first_red is not None and span['start_row'] < first_red]
    expected_top = plan['top'] * raw_to_capture
    checks['expected_top_row'] = expected_top
    checks['top_red_at_first_row'] = first_red == 0
    checks['top_red_after_notch'] = bool(
        first_red is not None
        and all(label == 'black' for label in before)
        and abs(first_red - expected_top) <= 2)
    checks['safe_bottom_reaches_screen'] = plan['bottom'] == plan['screen_height']
    checks['bottom_green_last_row'] = (spans[-1]['label'] == 'green'
                                       if checks['safe_bottom_reaches_screen'] else None)
    checks['checks_conclusive'] = (covers and checks['no_extra_overscan']
                                   and checks['green_marker_visible']
                                   and checks['top_red_after_notch']
                                   and (checks['bottom_green_last_row'] is True
                                        or not checks['safe_bottom_reaches_screen']))
    return checks


def take_owned_capture(payload, session, plan):
    """Capture and analyze the owned probe window while the probe is still alive.

    The probe's guest pid never matches the native window owner; the host pid is
    resolved from ps by the exact owned payload path plus probe basename, and only
    CGWindows owned by that pid are eligible. Returns a record dict, never raises.
    """
    record = {'captured': False}
    try:
        record['resolved_host_pids'] = resolve_owned_host_pids(payload.name)
        record['probe_guest_pid_note'] = ('probe stdout pid= is the Wine guest pid and is not '
                                          'used for window matching')
        if not record['resolved_host_pids']:
            record['error'] = 'owned probe host pid not resolvable from ps command match'
            return record
        state = run_geometry_helper()
        matches = owned_windows_for_host_pids(state, record['resolved_host_pids'])
        if not matches:
            record['error'] = ('no onscreen CGWindow owned by resolved probe host pids %s'
                               % record['resolved_host_pids'])
            return record
        window = max(matches, key=lambda row: int(
            (row.get('kCGWindowBounds') or {}).get('Width', 0)) *
            int((row.get('kCGWindowBounds') or {}).get('Height', 0)))
        window_id = window.get('kCGWindowNumber')
        png = session / 'window.png'
        shot = subprocess.run(['/usr/sbin/screencapture', '-x', '-o', '-l',
                               str(window_id), str(png)],
                              capture_output=True, text=True)
        record.update({'window': window, 'screencapture_exit': shot.returncode,
                       'png': str(png) if png.exists() else None})
        if shot.returncode != 0 or not png.exists():
            record['error'] = 'screencapture of the owned window failed'
            return record
        record['captured'] = True
        record['checks'] = analyze_capture(png, plan)
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        record['error'] = str(error)
    return record


def run_geometry_helper(timeout=5):
    result = subprocess.run([str(CANDIDATE / 'window-geometry')],
                            capture_output=True, text=True, timeout=timeout, check=True)
    return json.loads(result.stdout)


def verify_runtime_pins():
    """Verify every pin in the finalized candidate manifest before launch.

    Uses build/compat-adaptive-viewport/candidate-manifest.json (published by
    finalize_runtime), not the accepted baseline identities, because the adaptive
    runtime modules are rebuilt and their pins differ from the accepted delivery.
    """
    manifest = CANDIDATE / 'candidate-manifest.json'
    if not manifest.is_file():
        raise RuntimeError('Candidate manifest %s is not published yet; refusing launch'
                           % manifest)
    record = json.loads(manifest.read_text())
    if record.get('schema') != 'ra3-adaptive-viewport-candidate-v1':
        raise RuntimeError('Unknown candidate manifest schema: %r' % (record.get('schema'),))
    if 'status' not in record:
        raise RuntimeError('Candidate manifest lacks a status field')
    roots = {'runtime_pins': CANDIDATE / 'wine',
             'game_pins': CANDIDATE / 'prefix/drive_c/ra3-mtld3d',
             'payload_pins': CANDIDATE / 'prefix/drive_c/u60-mtld3d'}
    verified = {}
    for group, root in roots.items():
        pins = record.get(group)
        if not isinstance(pins, dict) or not pins:
            raise RuntimeError('Candidate manifest group %s is missing or empty' % group)
        for relative, expected in sorted(pins.items()):
            target = root / relative
            if not target.is_file() or digest(target) != expected:
                raise RuntimeError('Candidate %s pin mismatch or missing: %s' % (group, relative))
            verified['%s:%s' % (group, relative)] = 'ok'
    isolation = json.loads((CANDIDATE / 'isolation.json').read_text())
    if not isolation.get('no_shared_writable_prefix'):
        raise RuntimeError('Candidate isolation manifest no longer declares isolated prefix')
    return {'pins': verified, 'manifest': str(manifest),
            'manifest_status': record.get('status')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('new_session', type=Path, nargs='?')
    parser.add_argument('--width', type=int, default=None,
                        help='probe backbuffer request width; default is the planned safe viewport')
    parser.add_argument('--height', type=int, default=None,
                        help='probe backbuffer request height; default is the planned safe viewport')
    parser.add_argument('--pointer-check', action='store_true')
    parser.add_argument('--capture', action='store_true',
                        help='screenshot only the exact owned probe window (host pid resolved '
                             'via ps command match) and analyze marker rows')
    parser.add_argument('--self-test', action='store_true',
                        help='run pure validator fixtures and exit; no wine or display use')
    args = parser.parse_args()
    if args.self_test:
        print('SELF-TEST PASS (%d fixtures)' % self_test())
        return
    if args.new_session is None:
        parser.error('NEW_SESSION is required unless --self-test')

    if 'ra3ep1_1.0.game' in subprocess.check_output(
            ['ps', '-axww', '-o', 'comm='], text=True).lower():
        raise RuntimeError('Game active; refusing owned check')
    for helper in (CANDIDATE / 'display-plan', CANDIDATE / 'window-geometry', DISPLAY_RECOVERY,
                   CANDIDATE / 'wine/bin/wine', CANDIDATE / 'wine/bin/wineserver',
                   CANDIDATE / 'cursor_probe.exe'):
        if not helper.is_file():
            raise RuntimeError('Missing candidate component: %s' % helper)
    probes = [name for name in ('fullscreen_probe_readback.exe', 'fullscreen_probe.exe')
              if (CANDIDATE / name).is_file()]
    if not probes:
        raise RuntimeError('Candidate lacks an owned fullscreen probe executable')
    probe_source = probes[0]
    deployment = verify_runtime_pins()
    deployment['probe_source'] = probe_source
    deployment['probe_source_sha256'] = digest(CANDIDATE / probe_source)
    deployment['cursor_probe_sha256'] = digest(CANDIDATE / 'cursor_probe.exe')
    for module in ('win32u.so', 'winemac.so'):
        path = CANDIDATE / 'wine/lib/wine/x86_64-unix' / module
        deployment.setdefault('module_sha256', {})[module] = digest(path)

    session = args.new_session.absolute()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', session.name):
        raise RuntimeError('Session name must be a simple Windows-safe component')
    if os.path.lexists(session):
        raise RuntimeError('Output directory already exists')
    session.mkdir(parents=True, exist_ok=False)
    (session / 'home').mkdir()

    planner_run = subprocess.run([str(CANDIDATE / 'display-plan')],
                                 capture_output=True, text=True, timeout=15)
    (session / 'display-plan.json').write_text(planner_run.stdout)
    if planner_run.returncode != 0:
        raise RuntimeError('display-plan failed: %s' % planner_run.stderr.strip())
    raw_plan = json.loads(planner_run.stdout)
    plan = validate_plan(raw_plan)

    request_width = args.width if args.width is not None else plan['viewport_width']
    request_height = args.height if args.height is not None else plan['viewport_height']
    if not (320 <= request_width <= 8192 and 200 <= request_height <= 8192):
        raise RuntimeError('Unsupported probe dimensions')
    preflight = run_geometry_helper()
    active = fullscreen_wine_windows(preflight)
    if active:
        raise RuntimeError('Fullscreen wine window already active; refusing owned check: %s'
                           % json.dumps(active[0], sort_keys=True))

    payload = CANDIDATE / 'prefix/drive_c' / ('owned-' + session.name)
    payload.mkdir(exist_ok=False)
    deployment['payload'] = str(payload)
    for target_name, source_name in (('fullscreen_probe.exe', probe_source),
                                     ('cursor_probe.exe', 'cursor_probe.exe')):
        with (payload / target_name).open('xb') as stream:
            stream.write((CANDIDATE / source_name).read_bytes())
        if digest(payload / target_name) != digest(CANDIDATE / source_name):
            raise RuntimeError('Probe copy mismatch')

    env = {'HOME': str(session / 'home'), 'USER': os.environ.get('USER', 'rakan'),
           'LOGNAME': os.environ.get('LOGNAME', 'rakan'),
           'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TMPDIR': str(session),
           'WINEPREFIX': str(CANDIDATE / 'prefix'), 'WINEARCH': 'win64',
           'WINEMSYNC': '1', 'WINEDLLOVERRIDES': 'mscoree,mshtml=', 'WINEDEBUG': '-all',
           'RA3_NO_AUDIO_CAPTURE': '1',
           SPEC_ENV_NAME: plan['safe_viewport']}
    if 'RA3_FIXED_VIEWPORT' in env:
        raise RuntimeError('Legacy RA3_FIXED_VIEWPORT must not be set for the adaptive runtime')
    wine = str(CANDIDATE / 'wine/bin/wine')
    command = [wine, 'cmd', '/d', '/s', '/c',
               'C:\\' + payload.name + '\\fullscreen_probe.exe ' + str(request_width) + ' ' +
               str(request_height) + ' > C:\\' + payload.name + '\\fullscreen.txt 2>&1']
    desktop = session / 'desktop-before.json'
    subprocess.run([str(DISPLAY_RECOVERY), 'snapshot', str(desktop)], check=True)
    geometry = []
    pointer_child = None
    pointer_result = None
    timed_out = False
    wineserver_wait_timeout = False
    captured = None
    try:
        with (session / 'wine.log').open('xb') as log:
            child = subprocess.Popen(command, env=env, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT)
            started = time.monotonic()
            pointer_started = False
            while child.poll() is None:
                if time.monotonic() - started > 45:
                    # Stop only this owned launcher; no process-name-wide termination.
                    child.terminate()
                    timed_out = True
                    break
                try:
                    geometry.append(run_geometry_helper())
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    pass
                output = payload / 'fullscreen.txt'
                text_now = output.read_text(errors='replace') if output.exists() else ''
                if 'backbuffer=' in text_now and args.pointer_check and not pointer_started:
                    pointer_started = True
                    pointer_command = [wine, 'cmd', '/d', '/s', '/c',
                                       'C:\\' + payload.name + '\\cursor_probe.exe > C:\\' +
                                       payload.name + '\\pointer.txt 2>&1']
                    pointer_child = subprocess.Popen(pointer_command, env=env, cwd=ROOT,
                                                     stdout=log, stderr=subprocess.STDOUT)
                if args.capture and captured is None and 'backbuffer_top=' in text_now:
                    time.sleep(1)
                    # Screenshot while the owned probe window is alive and presenting markers.
                    captured = take_owned_capture(payload, session, plan)
                time.sleep(0.25)
            if timed_out:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            exit_code = child.returncode
            if pointer_child is not None:
                try:
                    pointer_child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    pointer_child.kill()
                    pointer_child.wait(timeout=15)
                pointer_result = pointer_child
            try:
                subprocess.run([str(CANDIDATE / 'wine/bin/wineserver'), '-w'],
                               env=env, cwd=ROOT, timeout=30, check=True)
            except subprocess.TimeoutExpired:
                # Bounded wait only; record it and continue to results. No name-wide kill.
                wineserver_wait_timeout = True
        text = (payload / 'fullscreen.txt').read_text(errors='replace')
        probe = parse_probe_output(text)
        drawables = renderer_drawables(payload)
        target_drawable = [plan['drawable_width'], plan['drawable_height']]
        target_guest_raw = [request_width, request_height]
        pointer_text = ((payload / 'pointer.txt').read_text(errors='replace')
                        if (payload / 'pointer.txt').exists() else None)
        record = {'command': command, 'environment': env,
                  'viewport_request': {'width': request_width, 'height': request_height,
                                       'defaulted': args.width is None and args.height is None},
                  'plan': plan, 'display_plan_file': str(session / 'display-plan.json'),
                  'deployment': deployment, 'timed_out': timed_out,
                  'wineserver_wait_timeout': wineserver_wait_timeout,
                  'exit_code': exit_code,
                  'probe': probe, 'output': text, 'native_geometry': geometry,
                  'renderer_drawables': drawables,
                  'target_drawable': target_drawable, 'target_guest_raw': target_guest_raw,
                  'backbuffer_matches_request': probe['backbuffer'] == target_guest_raw
                                                if probe['backbuffer'] else None,
                  'backbuffer_note': 'Native default backbuffer may differ from the raw request; '
                                     'renderer drawable is validated against the planned drawable.',
                  'pointer_exit': pointer_result.returncode if pointer_result else None,
                  'pointer_output': pointer_text}
        failures = []
        if timed_out:
            failures.append('Owned fullscreen check timed out')
        if wineserver_wait_timeout:
            failures.append('wineserver bounded wait timed out; owned wine state may linger')
        if exit_code != 42 or not probe['pass']:
            failures.append('Owned fullscreen drawing did not pass')
        if args.pointer_check and (pointer_result is None or pointer_result.returncode != 42
                                   or not pointer_text or 'PASS:' not in pointer_text):
            failures.append('Owned pointer round trips did not pass')
        if not drawables:
            failures.append('No renderer present lines found')
        else:
            if any(row['drawable'] != target_drawable for row in drawables):
                failures.append('Renderer drawable did not meet planned drawable target; '
                                'inspect result.json')
            if any(row['guest'] != target_guest_raw for row in drawables):
                failures.append('Renderer guest raw did not meet the raw request; '
                                'inspect result.json')
        capture_record = {'requested': bool(args.capture), 'captured': False}
        if captured is not None:
            capture_record.update(captured)
        if args.capture:
            if not capture_record.get('captured'):
                failures.append('Owned capture did not complete: %s'
                                % capture_record.get('error', 'no capture attempted'))
            elif not capture_record.get('checks', {}).get('checks_conclusive'):
                failures.append('Capture marker formulas failed; inspect result.json')
        record['capture'] = capture_record
        record['failures'] = failures
        record['acceptance'] = ('PASS' if not failures else 'FAIL: ' + '; '.join(failures))
        with (session / 'result.json').open('x') as stream:
            json.dump(record, stream, indent=2)
        print(text, flush=True)
        print(json.dumps({'acceptance': record['acceptance'],
                          'target_drawable': target_drawable,
                          'target_guest_raw': target_guest_raw,
                          'backbuffer': probe['backbuffer'],
                          'pointer': 'PASS' if (pointer_result and pointer_result.returncode == 42)
                                     else ('FAIL' if args.pointer_check else 'skipped'),
                          'capture': ('PASS' if capture_record.get('captured') and
                                      capture_record.get('checks', {}).get('checks_conclusive')
                                      else ('FAIL' if args.capture else 'skipped')),
                          'session': str(session)}, sort_keys=True), flush=True)
        if failures:
            raise RuntimeError(record['acceptance'])
    finally:
        game_active = 'ra3ep1_1.0.game' in subprocess.check_output(
            ['ps', '-axww', '-o', 'comm='], text=True).lower()
        if game_active:
            # Never restore the display mode while a real game is running.
            with (session / 'desktop-restore.json').open('x') as stream:
                json.dump({'skipped': True,
                           'reason': 'real game appeared; display restore refused'}, stream,
                          indent=2)
            print('DISPLAY RESTORE SKIPPED: real game active', flush=True)
        else:
            restored = subprocess.run([str(DISPLAY_RECOVERY), 'restore', str(desktop)],
                                      capture_output=True, text=True)
            with (session / 'desktop-restore.json').open('x') as stream:
                json.dump({'skipped': False, 'exit_code': restored.returncode,
                           'stdout': restored.stdout,
                           'stderr': restored.stderr}, stream, indent=2)
            print(restored.stdout or restored.stderr, flush=True)


if __name__ == '__main__':
    main()
