#!/usr/bin/env python3
"""Compile and exercise the actual adaptive viewport route from Wine sysparams.c.

Only the extracted production enum/functions are compiled. Small type and API
stubs replace the surrounding Wine headers; route parsing uses viewport_spec.h.
Each scenario runs in a fresh process because Wine caches its first route lookup.
"""

import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'build/compat-adaptive-viewport/wine-source/dlls/win32u/sysparams.c'
HEADER = ROOT / 'research/compat/adaptive-viewport/viewport_spec.h'
RESULT = ROOT / 'build/compat-adaptive-viewport/routecheckresult.json'


def _matching_brace(source, opening):
    """Return the matching C brace, skipping comments and quoted literals."""
    depth = 0
    index = opening
    quote = None
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ''
        if quote:
            if char == '\\':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '/' and following == '*':
            end = source.find('*/', index + 2)
            if end < 0:
                raise ValueError('unterminated C block comment')
            index = end + 2
            continue
        elif char == '/' and following == '/':
            end = source.find('\n', index + 2)
            index = len(source) if end < 0 else end + 1
            continue
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError('unbalanced C braces while extracting source')


def extract_c_construct(source, marker, semicolon_after_brace=False):
    """Extract one actual enum/function body, retaining source line coordinates."""
    if source.count(marker) != 1:
        raise ValueError('expected exactly one %r marker in %s' % (marker, SOURCE))
    start = source.index(marker)
    opening = source.find('{', start)
    if opening < 0:
        raise ValueError('no opening brace after %r' % marker)
    closing = _matching_brace(source, opening)
    end = closing + 1
    if semicolon_after_brace:
        semicolon = source.find(';', end)
        if semicolon < 0 or source[end:semicolon].strip():
            raise ValueError('expected enum semicolon after %r' % marker)
        end = semicolon + 1
    snippet = source[start:end]
    return {
        'text': snippet,
        'start_line': source.count('\n', 0, start) + 1,
        'end_line': source.count('\n', 0, end) + 1,
    }


def make_harness(route_enum, route_function, add_screen_size, get_screen_sizes):
    """Wrap extracted Wine code with only the declarations its C needs."""
    return r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "viewport_spec.h"

typedef unsigned int UINT;
typedef struct { int cx, cy; } SIZE;
typedef struct { UINT dmPelsWidth, dmPelsHeight; } DEVMODEW;
#define DM_PELSWIDTH 1
#define DM_PELSHEIGHT 2
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define NEXT_DEVMODEW(mode) ((mode) + 1)
#define TRACE(...) ((void)0)

static UINT devmode_get(const DEVMODEW *mode, UINT field)
{
    return field == DM_PELSWIDTH ? mode->dmPelsWidth : mode->dmPelsHeight;
}

''' + route_enum['text'] + '\n\n' + route_function['text'] + '\n\n' + add_screen_size['text'] + '\n\n' + get_screen_sizes['text'] + r'''

int main(int argc, char **argv)
{
    DEVMODEW maximum;
    SIZE *sizes;
    UINT count = 0, i;
    if (argc != 3) return 2;
    maximum.dmPelsWidth = (UINT)strtoul(argv[1], NULL, 10);
    maximum.dmPelsHeight = (UINT)strtoul(argv[2], NULL, 10);
    sizes = get_screen_sizes(&maximum, NULL, 0, &count);
    if (!sizes) return 3;
    for (i = 0; i < count; ++i)
        printf("%u %u\n", (unsigned int)sizes[i].cx, (unsigned int)sizes[i].cy);
    free(sizes);
    return 0;
}
'''


CASES = [
    {
        'name': 'unset_adds_no_legacy_mode',
        'maximum': [4096, 2160],
        'environment': {},
        'must_contain': [[4096, 2160]],
        'must_not_contain': [[3024, 1900]],
        'expected_counts': {'3024x1900': 0},
    },
    {
        'name': 'invalid_new_value_suppresses_legacy',
        'maximum': [4096, 2160],
        'environment': {'RA3_SAFE_VIEWPORT': 'not-a-spec',
                        'RA3_FIXED_VIEWPORT': '3024x1900'},
        'must_contain': [[4096, 2160]],
        'must_not_contain': [[3024, 1900]],
        'expected_counts': {'3024x1900': 0},
    },
    {
        'name': 'valid_new_value_wins_over_legacy',
        'maximum': [4096, 2160],
        'environment': {'RA3_SAFE_VIEWPORT': '4096x2160:0,160,2000,2160',
                        'RA3_FIXED_VIEWPORT': '3024x1900'},
        'must_contain': [[4096, 2160], [2000, 2000]],
        'must_not_contain': [[3024, 1900]],
        'expected_counts': {'2000x2000': 1, '3024x1900': 0},
    },
    {
        'name': 'legacy_exact_value_without_new_value',
        'maximum': [4096, 2160],
        'environment': {'RA3_FIXED_VIEWPORT': '3024x1900'},
        'must_contain': [[4096, 2160], [3024, 1900]],
        'must_not_contain': [],
        'expected_counts': {'3024x1900': 1},
    },
    {
        'name': 'portrait_safe_mode_is_normalized',
        'maximum': [3456, 4608],
        'environment': {'RA3_SAFE_VIEWPORT': '3456x4608:0,64,3456,4608'},
        'must_contain': [[4608, 3456], [4544, 3456]],
        'must_not_contain': [[3456, 4544]],
        'expected_counts': {'4544x3456': 1, '3456x4544': 0},
    },
    {
        'name': 'valid_spec_must_match_raw_maximum_dimensions',
        'maximum': [4096, 2160],
        'environment': {'RA3_SAFE_VIEWPORT': '3840x2160:0,0,3840,2160',
                        'RA3_FIXED_VIEWPORT': '3024x1900'},
        'must_contain': [[4096, 2160]],
        'must_not_contain': [[3840, 2160], [3024, 1900]],
        'expected_counts': {'3840x2160': 0, '3024x1900': 0},
    },
]


def run_case(binary, case):
    env = os.environ.copy()
    env.pop('RA3_SAFE_VIEWPORT', None)
    env.pop('RA3_FIXED_VIEWPORT', None)
    env.update(case['environment'])
    completed = subprocess.run(
        [str(binary), str(case['maximum'][0]), str(case['maximum'][1])],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError('%s exited %d: %s' % (
            case['name'], completed.returncode, completed.stderr.strip()))
    try:
        sizes = [[int(field) for field in line.split()]
                 for line in completed.stdout.splitlines() if line.strip()]
    except ValueError as error:
        raise RuntimeError('%s emitted malformed sizes: %r' % (
            case['name'], completed.stdout)) from error
    size_counts = {}
    for width, height in sizes:
        key = '%dx%d' % (width, height)
        size_counts[key] = size_counts.get(key, 0) + 1
    checks = {}
    for size in case['must_contain']:
        checks['contains_%dx%d' % tuple(size)] = size in sizes
    for size in case['must_not_contain']:
        checks['omits_%dx%d' % tuple(size)] = size not in sizes
    for size, expected in case['expected_counts'].items():
        checks['count_%s_is_%d' % (size, expected)] = size_counts.get(size, 0) == expected
    return {
        'name': case['name'],
        'maximum_raw_dimensions': case['maximum'],
        'environment': case['environment'],
        'sizes': sizes,
        'checks': checks,
        'passed': all(checks.values()),
    }


def main():
    global SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine-source", type=Path)
    args = parser.parse_args()
    if args.wine_source:
        SOURCE = args.wine_source.resolve() / "dlls/win32u/sysparams.c"
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    if not SOURCE.is_file():
        raise FileNotFoundError('Wine source is missing: %s' % SOURCE)
    if not HEADER.is_file():
        raise FileNotFoundError('Viewport contract header is missing: %s' % HEADER)
    compiler = shutil.which('clang')
    if not compiler:
        raise RuntimeError('clang is required to compile the extracted Wine C code')

    source = SOURCE.read_text()
    route_enum = extract_c_construct(source, 'enum ra3_viewport_route', True)
    route_function = extract_c_construct(source, 'static int ra3_viewport_route(')
    add_screen_size = extract_c_construct(source, 'static UINT add_screen_size(')
    get_screen_sizes = extract_c_construct(source, 'static SIZE *get_screen_sizes(')
    snippets = [route_enum, route_function, add_screen_size, get_screen_sizes]
    extracted_source = '\n\n'.join(snippet['text'] for snippet in snippets)
    harness = make_harness(route_enum, route_function, add_screen_size, get_screen_sizes)
    result = {
        'schema': 'ra3-wine-viewport-route-check-v1',
        'source': str(SOURCE),
        'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'contract_header': str(HEADER),
        'extraction': {
            'method': 'balanced-brace extraction from exact named constructs',
            'constructs': [
                {'name': 'route enum', **route_enum},
                {'name': 'cached environment selector', **route_function},
                {'name': 'add_screen_size', **add_screen_size},
                {'name': 'get_screen_sizes', **get_screen_sizes},
            ],
            'extracted_code_sha256': hashlib.sha256(extracted_source.encode()).hexdigest(),
        },
        'compiler': compiler,
        'compile_command': [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(HEADER.parent), '<temporary-harness.c>',
                            '-o', '<temporary-route-check>'],
        'cases': [],
    }

    RESULT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ra3-wine-route-check-') as temporary:
        temporary = Path(temporary)
        c_file = temporary / 'actual_wine_route_harness.c'
        binary = temporary / 'actual_wine_route_check'
        c_file.write_text(harness)
        command = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                   '-I', str(HEADER.parent), str(c_file), '-o', str(binary)]
        compilation = subprocess.run(command, cwd=ROOT, text=True,
                                     capture_output=True, check=False)
        result['compile'] = {
            'exit_code': compilation.returncode,
            'stderr': compilation.stderr,
            'temporary_binary': str(binary),
            'binary_sha256': (hashlib.sha256(binary.read_bytes()).hexdigest()
                              if binary.is_file() else None),
        }
        if compilation.returncode:
            result['status'] = 'FAIL'
            result['cases'] = []
            RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
            raise RuntimeError('clang failed; see %s: %s' % (
                RESULT, compilation.stderr.strip()))
        result['cases'] = [run_case(binary, case) for case in CASES]

    failures = [case['name'] for case in result['cases'] if not case['passed']]
    result['status'] = 'FAIL' if failures else 'PASS'
    result['case_count'] = len(result['cases'])
    result['failed_cases'] = failures
    RESULT.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': result['status'], 'case_count': result['case_count'],
                      'failed_cases': failures, 'result': str(RESULT)}, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
