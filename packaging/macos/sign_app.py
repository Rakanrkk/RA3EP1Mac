"""Sign the local app and bundled Mach-O images, then refresh component pins."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

MACH_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
               b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
               b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--identity', default='-', help='Existing signing identity hash, or - for ad-hoc')
    args = parser.parse_args()
    app = args.app.absolute()
    if not app.is_dir() or app.suffix != '.app':
        raise RuntimeError('Expected an existing app bundle')
    if args.report.exists():
        raise RuntimeError('Report already exists')
    # Finder metadata is not part of this generated delivery and prevents code signing.
    for attribute in ('com.apple.FinderInfo', 'com.apple.ResourceFork'):
        subprocess.run(['/usr/bin/xattr', '-r', '-d', attribute, str(app)],
                       capture_output=True, text=True)
    binaries = []
    for root in (app / 'Contents/MacOS', app / 'Contents/Resources/wine'):
        for path in root.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open('rb') as stream:
                if stream.read(4) in MACH_MAGICS:
                    binaries.append(path)
    helper = app / 'Contents/Resources/display-mode'
    if helper.is_file():
        binaries.append(helper)
    records = []
    for path in sorted(set(binaries), key=lambda value: len(value.parts), reverse=True):
        result = subprocess.run(['/usr/bin/codesign', '--force', '--sign', args.identity,
                                 '--timestamp=none', str(path)], capture_output=True, text=True)
        records.append({'path': str(path.relative_to(app)), 'exit_code': result.returncode,
                        'stderr': result.stderr})
        if result.returncode:
            raise RuntimeError('Signing failed: ' + str(path) + '\n' + result.stderr)
    manifest_path = app / 'Contents/Resources/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for relative in manifest['runtime_pins']:
        path = app / 'Contents/Resources/wine' / relative
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        manifest['runtime_pins'][relative] = digest.hexdigest()
    manifest['signature_policy'] = 'Local signing identity ' + args.identity + '; pins refreshed after signing'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', args.identity, '--timestamp=none', str(app)],
                   check=True, capture_output=True, text=True)
    verified = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2', str(app)],
                              capture_output=True, text=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'local_only': True, 'identity': args.identity, 'signed_images': records,
                                      'bundle_verify_exit': verified.returncode,
                                      'verify_stdout': verified.stdout,
                                      'verify_stderr': verified.stderr}, indent=2) + '\n')
    if verified.returncode:
        raise RuntimeError('Bundle signature verification failed: ' + verified.stderr)
    print('Signed', len(records), 'Mach-O images; bundle signature verifies.')


if __name__ == '__main__':
    main()
