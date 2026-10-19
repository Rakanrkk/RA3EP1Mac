"""Synthetic archive and RefPack tests; never read or run the game."""

import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import unittest.mock

import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("compat_resources", Path(__file__).with_name("compat_resources.py"))
compat = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compat
_spec.loader.exec_module(compat)


class RefPackTests(unittest.TestCase):
    def test_short_overlap_and_long_header(self):
        short = bytes.fromhex('10fb000008e0') + b'abcd' + bytes.fromhex('0403fc')
        self.assertEqual(compat.refpack_unpack(short), b'abcdabcd')
        payload = b'0123456789'
        long = (b'\x90\xfb' + len(payload).to_bytes(4, 'big') +
                bytes((0xe1,)) + payload[:8] + bytes((0xfe,)) + payload[8:])
        self.assertEqual(compat.refpack_unpack(long), payload)

    def test_rejects_truncated_and_inconsistent_streams(self):
        samples = (
            bytes.fromhex('10fb000005e0') + b'ab',
            bytes.fromhex('10fb000005e0') + b'abcd',
            bytes.fromhex('10fb00000400'),
            bytes.fromhex('10fb000004fc') + b'abcd',
        )
        for sample in samples:
            with self.subTest(sample=sample.hex()):
                with self.assertRaises(compat.ResourceError):
                    compat.refpack_unpack(sample)

    def test_literal_suffix_sizes_and_unchanged_prefix(self):
        with tempfile.TemporaryDirectory(prefix='compat-refpack-') as directory:
            root = Path(directory)
            for size in (1, 2, 3, 4, 112, 113, 116, 4096):
                with self.subTest(size=size):
                    raw = bytes(index % 251 for index in range(size + 9))
                    source = root / 'decoded'
                    encoded = root / 'encoded'
                    source.write_bytes(raw)
                    prefix = bytes.fromhex('10fb000000') + b'unchanged-prefix'
                    compat.encode_literal_suffix(source, 9, size, encoded)
                    stream = (bytes.fromhex('10fb') + (size + 4).to_bytes(3, 'big') +
                              bytes((0xe0,)) + b'head' + encoded.read_bytes())
                    self.assertEqual(compat.refpack_unpack(stream), b'head' + raw[9:])
                    self.assertEqual(prefix, bytes.fromhex('10fb000000') + b'unchanged-prefix')

    def test_decode_file_range_and_token_boundary(self):
        decoded = b'abcdefghabcdefgh-tail'
        packed = _refpack_literal(decoded)
        with tempfile.TemporaryDirectory(prefix='compat-refpack-range-') as directory:
            root = Path(directory)
            archive = root / 'fixture.big'
            output = root / 'decoded.bin'
            _write_big(archive, [('data/stream.bin', packed)])
            entry = compat.big_catalog_file(archive)[0]
            result = compat.decode_refpack_range(
                archive, entry[1], entry[2], output, stop_at=6)
            self.assertEqual(output.read_bytes(), decoded)
            self.assertEqual(result['decoded_size'], len(decoded))
            self.assertEqual(result['boundary'], (5, 0))


class BigArchiveTests(unittest.TestCase):
    def test_catalog_and_append_repoint_keeps_other_entries(self):
        original = _big_bytes([('alpha.bin', b'alpha'), ('target.bin', b'old'),
                               ('nested/omega.bin', b'omega')])
        before = compat.big_catalog_bytes(original)
        candidate = compat.replace_big_entry_bytes(original, 'target.bin', b'new payload')
        after = compat.big_catalog_bytes(candidate)
        self.assertEqual([entry[0] for entry in before], [entry[0] for entry in after])
        self.assertEqual(_big_member(candidate, 'target.bin'), b'new payload')
        for name in ('alpha.bin', 'nested/omega.bin'):
            self.assertEqual(_big_member(candidate, name), _big_member(original, name))
        old_ranges = {entry[0]: entry[1:3] for entry in before}
        new_ranges = {entry[0]: entry[1:3] for entry in after}
        self.assertEqual(old_ranges['alpha.bin'], new_ranges['alpha.bin'])
        self.assertEqual(old_ranges['nested/omega.bin'], new_ranges['nested/omega.bin'])
        self.assertEqual(new_ranges['target.bin'], (len(original), len(b'new payload')))

    def test_rejects_bad_index_duplicate_and_unsafe_names(self):
        for fixture in (
            b'nope' + b'\0' * 32,
            _big_bytes([('../escape', b'x')]),
            _big_bytes([('same', b'x'), ('same', b'y')]),
        ):
            with self.subTest(fixture=fixture[:24]):
                with self.assertRaises(compat.ResourceError):
                    compat.big_catalog_bytes(fixture)

    def test_streaming_index_parse_handles_large_archive_tail(self):
        raw = _big_bytes([('one.bin', b'x')])
        with tempfile.TemporaryDirectory(prefix='compat-big-index-') as directory:
            path = Path(directory) / 'with-large-tail.big'
            path.write_bytes(raw + b'Z' * (2 * 1024 * 1024))
            entries = compat.big_catalog_file(path)
            self.assertEqual(entries[0][0], 'one.bin')
            self.assertEqual(entries[0][2], 1)

    def test_bloom_transform_is_exact_and_digest_gated(self):
        script = _make_bloom_script()
        expected_hash = hashlib.sha256(script).hexdigest()
        patched = compat._patch_bloom_script(script, expected_hash)
        self.assertEqual(patched.count(b'A8R8G8B8'), 6)
        self.assertEqual(patched.count(b'$PostEffect.Bloom.TextureFormat'), 0)
        self.assertEqual(patched.count(b'callproc PostEffectBloom'), 2)
        self.assertEqual(compat._restore_bloom_lines(patched), script)
        with self.assertRaises(compat.ResourceError):
            compat._patch_bloom_script(script + b'changed', expected_hash)
        with self.assertRaises(compat.ResourceError):
            compat.patch_bloom_script(script)

    def test_bloom_big_repoints_only_target(self):
        script = _make_bloom_script()
        raw = _big_bytes([('keep.bin', b'other'),
                          (compat.BLOOM_TARGET, _refpack_literal(script))])
        patched = compat._patch_bloom_archive(raw, hashlib.sha256(script).hexdigest())
        before = compat.big_catalog_bytes(raw)
        after = compat.big_catalog_bytes(patched)
        self.assertEqual([row[0] for row in before], [row[0] for row in after])
        self.assertEqual(_big_member(patched, 'keep.bin'), b'other')
        self.assertEqual(compat.refpack_unpack(_big_member(patched, compat.BLOOM_TARGET)),
                         compat._patch_bloom_script(script, hashlib.sha256(script).hexdigest()))

    def test_static_archive_patch_changes_only_glow_interval_values(self):
        asset = bytearray(compat.GLOW_ASSET_SIZE)
        struct.pack_into('<2f', asset, 0x3c, 15.0, 15.0)
        struct.pack_into('<2f', asset, 0x48, 70.0, 70.0)
        struct.pack_into('<2f', asset, 0x60, 15.0, 15.0)
        struct.pack_into('<2f', asset, 0x6c, 1.0, 1.0)
        asset[0x80:0x80 + len(b'GPUParticle.fx\0')] = b'GPUParticle.fx\0'
        decoded_stream = b'12345678' + bytes(asset)
        manifest = _static_manifest(len(asset))
        original = _big_bytes([
            ('data/static.manifest', _refpack_literal(manifest)),
            ('data/static.bin', _refpack_literal(decoded_stream)),
            ('untouched.bin', b'other static data'),
        ])
        with tempfile.TemporaryDirectory(prefix='compat-static-fixture-') as directory:
            root = Path(directory)
            source, output = root / 'StaticStream.big', root / 'patched.big'
            source.write_bytes(original)
            details = compat.patch_static_archive(source, output, root)
            patched_entries = compat.big_catalog_file(output)
            patched = output.read_bytes()
            patched_stream = compat.refpack_unpack(_big_member(patched, 'data/static.bin'))
            changed = [index for index, pair in enumerate(zip(decoded_stream, patched_stream))
                       if pair[0] != pair[1]]
            self.assertEqual(changed, [8 + 0x62, 8 + 0x66])
            self.assertEqual(struct.unpack_from('<2f', patched_stream, 8 + 0x60), (14.0, 14.0))
            self.assertEqual(_big_member(patched, 'untouched.bin'), b'other static data')
            self.assertEqual(details['changed_asset_offsets'], ['0x60', '0x64'])
            self.assertEqual(details['verified_other_entries'], 2)
            self.assertEqual([entry[0] for entry in patched_entries],
                             ['data/static.manifest', 'data/static.bin', 'untouched.bin'])


class LightVariantTests(unittest.TestCase):
    """Synthetic high-refresh light fixtures; every pin is derived per fixture."""

    def _decoded_stream(self, asset=None):
        asset = _light_asset() if asset is None else asset
        return b'12345678' + asset

    def _write_source(self, root, decoded_stream, name_hash=None):
        packed = _refpack_literal(decoded_stream)
        manifest = _static_manifest_for([
            (compat.LIGHT_ASSET, compat.LIGHT_TYPE,
             compat.LIGHT_NAME_HASH if name_hash is None else name_hash,
             compat.LIGHT_ASSET_SIZE)])
        original = _big_bytes([
            ('data/static.manifest', _refpack_literal(manifest)),
            ('data/static.bin', packed),
            ('untouched.bin', b'other static data'),
        ])
        source = root / 'StaticStream.big'
        source.write_bytes(original)
        return (source, hashlib.sha256(packed).hexdigest(),
                hashlib.sha256(decoded_stream).hexdigest())

    def test_light_patch_appends_bank_and_changes_only_two_floats(self):
        decoded = self._decoded_stream()
        with tempfile.TemporaryDirectory(prefix='compat-light-') as directory:
            root = Path(directory)
            source, packed_sha, decoded_sha = self._write_source(root, decoded)
            source_bytes = source.read_bytes()
            output = root / 'light.big'
            details = compat.patch_static_light_archive(
                source, output, root, packed_bank_sha=packed_sha,
                decoded_bank_sha=decoded_sha)
            self.assertEqual(source.read_bytes(), source_bytes)
            expected = bytearray(decoded)
            expected[8 + 0x60:8 + 0x68] = struct.pack('<2f', 0.0, 0.0)
            expected = bytes(expected)
            changed = [index for index, (before, after) in enumerate(zip(decoded, expected))
                       if before != after]
            self.assertEqual(changed, [8 + 0x62, 8 + 0x63, 8 + 0x66, 8 + 0x67])
            raw = output.read_bytes()
            entries = compat.big_catalog_bytes(raw)
            self.assertEqual([entry[0] for entry in entries],
                             ['data/static.manifest', 'data/static.bin', 'untouched.bin'])
            self.assertEqual(_big_member(raw, 'data/static.bin'), expected)
            self.assertEqual(_big_member(raw, 'untouched.bin'), b'other static data')
            self.assertEqual(_big_member(raw, 'data/static.manifest'),
                             _big_member(source_bytes, 'data/static.manifest'))
            new_offset, new_size, _ = entries[1][1:]
            self.assertEqual((new_offset, new_size), (len(source_bytes), len(expected)))
            self.assertEqual(details['asset'], 'FXParticleSystemTemplate:JapanPowerPlantIdleLight')
            self.assertEqual(details['asset_offset'], 8)
            self.assertEqual(details['changed_asset_offsets'], ['0x60', '0x64'])
            self.assertEqual(details['decoded_stream_size'], len(decoded))
            self.assertEqual(details['verified_other_entries'], 2)
            self.assertEqual(details['source_packed_bank_sha256'], packed_sha)

    def test_light_patch_rejects_tampered_packed_bank(self):
        decoded = self._decoded_stream()
        with tempfile.TemporaryDirectory(prefix='compat-light-bad-') as directory:
            root = Path(directory)
            source, packed_sha, decoded_sha = self._write_source(root, decoded)
            raw = bytearray(source.read_bytes())
            entry = next(item for item in compat.big_catalog_bytes(bytes(raw))
                         if item[0] == 'data/static.bin')
            raw[entry[1] + entry[2] - 1] ^= 0xff
            source.write_bytes(bytes(raw))
            output = root / 'light.big'
            with self.assertRaises(compat.ResourceError):
                compat.patch_static_light_archive(
                    source, output, root, packed_bank_sha=packed_sha,
                    decoded_bank_sha=decoded_sha)
            self.assertFalse(output.exists())

    def test_light_patch_rejects_unexpected_decoded_bank(self):
        decoded = self._decoded_stream()
        with tempfile.TemporaryDirectory(prefix='compat-light-dec-') as directory:
            root = Path(directory)
            source, packed_sha, _ = self._write_source(root, decoded)
            output = root / 'light.big'
            with self.assertRaises(compat.ResourceError):
                compat.patch_static_light_archive(
                    source, output, root, packed_bank_sha=packed_sha,
                    decoded_bank_sha='0' * 64)
            self.assertFalse(output.exists())

    def test_light_patch_rejects_unexpected_template_fields(self):
        asset = bytearray(_light_asset())
        struct.pack_into('<2f', asset, 0x60, 2.0, 2.0)
        decoded = self._decoded_stream(bytes(asset))
        with tempfile.TemporaryDirectory(prefix='compat-light-fields-') as directory:
            root = Path(directory)
            source, packed_sha, decoded_sha = self._write_source(root, decoded)
            output = root / 'light.big'
            with self.assertRaises(compat.ResourceError):
                compat.patch_static_light_archive(
                    source, output, root, packed_bank_sha=packed_sha,
                    decoded_bank_sha=decoded_sha)
            self.assertFalse(output.exists())

    def test_light_patch_rejects_wrong_template_name_hash(self):
        decoded = self._decoded_stream()
        with tempfile.TemporaryDirectory(prefix='compat-light-name-') as directory:
            root = Path(directory)
            source, packed_sha, decoded_sha = self._write_source(
                root, decoded, name_hash=compat.LIGHT_NAME_HASH ^ 0x5a)
            output = root / 'light.big'
            with self.assertRaises(compat.ResourceError):
                compat.patch_static_light_archive(
                    source, output, root, packed_bank_sha=packed_sha,
                    decoded_bank_sha=decoded_sha)
            self.assertFalse(output.exists())

    def test_light_patch_refuses_already_light_input(self):
        decoded = self._decoded_stream()
        with tempfile.TemporaryDirectory(prefix='compat-light-again-') as directory:
            root = Path(directory)
            source, packed_sha, decoded_sha = self._write_source(root, decoded)
            first = root / 'light.big'
            compat.patch_static_light_archive(
                source, first, root, packed_bank_sha=packed_sha,
                decoded_bank_sha=decoded_sha)
            second = root / 'light-again.big'
            with self.assertRaises(compat.ResourceError):
                compat.patch_static_light_archive(
                    first, second, root, packed_bank_sha=packed_sha,
                    decoded_bank_sha=decoded_sha)
            self.assertFalse(second.exists())
            with first.open('rb') as stream:
                stream.seek(len(source.read_bytes()) + 8 + 0x60)
                self.assertEqual(stream.read(8), struct.pack('<2f', 0.0, 0.0))

    def test_light_state_pins_and_classifier(self):
        self.assertEqual(compat.LIGHT_CONTRACTS[2].patched_sha256, compat.LIGHT_TARGET_SHA)
        self.assertNotEqual(compat.LIGHT_CONTRACTS[2].patched_sha256,
                            compat.CONTRACTS[2].patched_sha256)
        for index in (0, 1):
            self.assertEqual(compat.LIGHT_CONTRACTS[index], compat.CONTRACTS[index])
        self.assertEqual(compat._light_static_state(compat.STATIC_CONTRACT.original_sha256),
                         'original')
        self.assertEqual(compat._light_static_state(compat.STATIC_CONTRACT.patched_sha256),
                         'accepted_soviet_glow')
        self.assertEqual(compat._light_static_state(compat.LIGHT_TARGET_SHA),
                         'already_high_refresh_light')
        with self.assertRaises(compat.ResourceError):
            compat._light_static_state('f' * 64)

    def test_light_mode_refuses_unknown_static_input(self):
        with tempfile.TemporaryDirectory(prefix='compat-light-gen-') as directory:
            root = Path(directory)
            data = root / 'game' / 'Data'
            data.mkdir(parents=True)
            for contract in compat.CONTRACTS:
                (data / contract.name).write_bytes(b'unknown:' + contract.name.encode())
            output = root / 'new-output'
            with self.assertRaises(compat.ResourceError):
                compat.generate_resources(data.parent, output, high_refresh_light=True)
            self.assertFalse(output.exists())

    def test_original_static_is_accepted_then_light_patched(self):
        glow = _glow_asset()
        light = _light_asset()
        decoded = b'12345678' + glow + light
        manifest = _static_manifest_for([
            (compat.GLOW_ASSET, compat.GLOW_TYPE, compat.GLOW_NAME_HASH,
             compat.GLOW_ASSET_SIZE),
            (compat.LIGHT_ASSET, compat.LIGHT_TYPE, compat.LIGHT_NAME_HASH,
             compat.LIGHT_ASSET_SIZE),
        ])
        source_bytes = _big_bytes([
            ('data/static.manifest', _refpack_literal(manifest)),
            ('data/static.bin', _refpack_literal(decoded)),
            ('untouched.bin', b'original archive member'),
        ])
        with tempfile.TemporaryDirectory(prefix='compat-light-chain-') as directory:
            root = Path(directory)
            source = root / 'original.big'
            source.write_bytes(source_bytes)
            accepted = root / 'accepted.big'
            compat.patch_static_archive(source, accepted, root)
            accepted_entries = compat.big_catalog_file(accepted)
            static_entry = next(row for row in accepted_entries
                                if row[0] == 'data/static.bin')
            packed_sha = compat._member_sha256(accepted, static_entry)
            decoded_accepted = root / 'accepted.decoded'
            compat.decode_refpack_range(accepted, static_entry[1], static_entry[2],
                                        decoded_accepted)
            decoded_sha = compat.sha256_file(decoded_accepted)
            accepted_bank = decoded_accepted.read_bytes()
            self.assertEqual(struct.unpack_from('<2f', accepted_bank, 8 + 0x60),
                             (14.0, 14.0))
            self.assertEqual(struct.unpack_from('<2f', accepted_bank, 8 + 552 + 0x60),
                             (1.0, 1.0))

            final = root / 'light.big'
            details = compat.patch_static_light_archive(
                accepted, final, root, packed_bank_sha=packed_sha,
                decoded_bank_sha=decoded_sha)
            final_bank = _big_member(final.read_bytes(), 'data/static.bin')
            self.assertEqual(struct.unpack_from('<2f', final_bank, 8 + 0x60),
                             (14.0, 14.0))
            self.assertEqual(struct.unpack_from('<2f', final_bank, 8 + 552 + 0x60),
                             (0.0, 0.0))
            expected = bytearray(accepted_bank)
            expected[8 + 552 + 0x60:8 + 552 + 0x68] = struct.pack('<2f', 0.0, 0.0)
            self.assertEqual(final_bank, bytes(expected))
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(details['source_packed_bank_sha256'], packed_sha)

    def test_generator_copies_already_light_inputs_through(self):
        contents = {
            'Shaders.big': b'synthetic accepted shaders',
            'Misc.big': b'synthetic accepted misc',
            'StaticStream.big': b'synthetic already-light static',
        }
        fixture_contracts = tuple(
            compat.ArchiveContract(name, '0' * 64,
                                   hashlib.sha256(contents[name]).hexdigest())
            for name in ('Shaders.big', 'Misc.big', 'StaticStream.big'))
        fixture_target = fixture_contracts[2].patched_sha256
        with tempfile.TemporaryDirectory(prefix='compat-light-copy-') as directory:
            root = Path(directory)
            data = root / 'game' / 'Data'
            data.mkdir(parents=True)
            for name, content in contents.items():
                (data / name).write_bytes(content)
            source_snapshot = {name: (data / name).read_bytes() for name in contents}
            output = root / 'resources'
            with unittest.mock.patch.multiple(
                    compat, LIGHT_TARGET_SHA=fixture_target,
                    LIGHT_CONTRACTS=fixture_contracts):
                result = compat.generate_resources(data.parent, output,
                                                   high_refresh_light=True)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['static_stream_variant'], 'high-refresh-light')
            self.assertEqual(result['target_pins']['StaticStream.big'], fixture_target)
            self.assertEqual(result['archives'][2]['input_state'],
                             'already_high_refresh_light')
            self.assertEqual(result['archives'][2]['operation'],
                             'copied_high_refresh_light_input')
            for name, content in contents.items():
                self.assertEqual((data / name).read_bytes(), source_snapshot[name])
                self.assertEqual((output / name).read_bytes(), content)

    def test_manifest_locator_resolves_both_templates(self):
        manifest = _static_manifest_for([
            (compat.GLOW_ASSET, compat.GLOW_TYPE, compat.GLOW_NAME_HASH, 16),
            (compat.LIGHT_ASSET, compat.LIGHT_TYPE, compat.LIGHT_NAME_HASH, 16),
        ])
        glow = compat._find_static_asset(manifest, compat.GLOW_ASSET, compat.GLOW_TYPE,
                                         compat.GLOW_NAME_HASH, 16, 'SovietPowerGlow')
        light = compat._find_static_asset(manifest, compat.LIGHT_ASSET, compat.LIGHT_TYPE,
                                          compat.LIGHT_NAME_HASH, 16, 'JapanPowerPlantIdleLight')
        self.assertEqual(glow['asset_index'], 0)
        self.assertEqual(glow['asset_offset'], 8)
        self.assertEqual(light['asset_index'], 1)
        self.assertEqual(light['asset_offset'], 8 + 16)
        self.assertEqual(glow['decoded_size'], 8 + 32)
        self.assertEqual(light['decoded_size'], 8 + 32)
        for name, type_code, name_hash, size in (
                (compat.LIGHT_ASSET, compat.LIGHT_TYPE, compat.LIGHT_NAME_HASH, 15),
                (compat.LIGHT_ASSET, compat.LIGHT_TYPE, compat.LIGHT_NAME_HASH ^ 1, 16),
                (b'FXParticleSystemTemplate:Missing', compat.LIGHT_TYPE,
                 compat.LIGHT_NAME_HASH, 16)):
            with self.subTest(name=name):
                bad = _static_manifest_for([
                    (compat.GLOW_ASSET, compat.GLOW_TYPE, compat.GLOW_NAME_HASH, 16),
                    (name, type_code, name_hash, size)])
                with self.assertRaises(compat.ResourceError):
                    compat._find_static_asset(bad, compat.LIGHT_ASSET, compat.LIGHT_TYPE,
                                              compat.LIGHT_NAME_HASH, 16, 'Light')


class GenerationSafetyTests(unittest.TestCase):
    def test_unknown_input_refused_before_output_creation(self):
        with tempfile.TemporaryDirectory(prefix='compat-resources-') as directory:
            root = Path(directory)
            data = root / 'game' / 'Data'
            data.mkdir(parents=True)
            for contract in compat.CONTRACTS:
                (data / contract.name).write_bytes(b'unknown:' + contract.name.encode())
            output = root / 'new-output'
            with self.assertRaises(compat.ResourceError):
                compat.generate_resources(data.parent, output)
            self.assertFalse(output.exists())

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='compat-output-exists-') as directory:
            root = Path(directory)
            data = root / 'game' / 'Data'
            data.mkdir(parents=True)
            for contract in compat.CONTRACTS:
                (data / contract.name).write_bytes(b'unknown')
            output = root / 'existing'
            output.mkdir()
            marker = output / 'keep'
            marker.write_text('do not overwrite')
            with self.assertRaises(compat.ResourceError):
                compat.generate_resources(data.parent, output)
            self.assertEqual(marker.read_text(), 'do not overwrite')

    def test_copy_accepted_archive_into_new_output(self):
        with tempfile.TemporaryDirectory(prefix='compat-copy-') as directory:
            root = Path(directory)
            source = root / 'source.big'
            content = b'accepted output fixture'
            source.write_bytes(content)
            output = root / 'copy.big'
            digest = hashlib.sha256(content).hexdigest()
            self.assertEqual(compat._copy_verified(source, output, digest), digest)
            self.assertEqual(output.read_bytes(), content)

    def test_rejects_mismatched_output_hash(self):
        with tempfile.TemporaryDirectory(prefix='compat-copy-mismatch-') as directory:
            source = Path(directory) / 'source.big'
            source.write_bytes(b'not pinned')
            with self.assertRaises(compat.ResourceError):
                compat._copy_verified(source, Path(directory) / 'copy.big', '0' * 64)


def _big_bytes(entries):
    encoded_names = [name.replace('\\', '/').encode('ascii') + b'\0' for name, _ in entries]
    index_end = 16 + sum(8 + len(name) for name in encoded_names)
    payloads = [payload for _, payload in entries]
    offsets = []
    current = index_end
    for payload in payloads:
        offsets.append(current)
        current += len(payload)
    if current > 0xffffffff:
        raise ValueError('synthetic BIG too large')
    result = bytearray(b'BIG4' + struct.pack('<I', current) +
                       struct.pack('>II', len(entries), index_end))
    for (name, payload), encoded, offset in zip(entries, encoded_names, offsets):
        result.extend(struct.pack('>II', offset, len(payload)))
        result.extend(encoded)
    for payload in payloads:
        result.extend(payload)
    return bytes(result)


def _write_big(path, entries):
    Path(path).write_bytes(_big_bytes(entries))


def _big_member(raw, name):
    hits = [entry for entry in compat.big_catalog_bytes(raw) if entry[0] == name]
    if len(hits) != 1:
        raise AssertionError('expected exactly one %s' % name)
    _, offset, size, _ = hits[0]
    return raw[offset:offset + size]


def _refpack_literal(payload):
    payload = bytes(payload)
    if not payload or len(payload) > 0xffffff:
        raise ValueError('fixture must fit non-empty RefPack 10FB size header')
    result = bytearray(b'\x10\xfb' + len(payload).to_bytes(3, 'big'))
    position = 0
    while len(payload) - position >= 4:
        size = min((len(payload) - position) // 4 * 4, 112)
        result.append(0xe0 + (size - 4) // 4)
        result.extend(payload[position:position + size])
        position += size
    remainder = len(payload) - position
    result.append(0xfc + remainder)
    result.extend(payload[position:])
    return bytes(result)


def _make_bloom_script():
    rows = []
    for level in ('High', 'Medium', 'Low'):
        for index in (0, 1):
            rows.append(('var texture BloomTexture%s%d tex%s = '
                         '$PostEffect.Bloom.TextureFormat\r\n' % (level, index, level)).encode())
    return (b'header\r\n' + b''.join(rows) +
            b'callproc PostEffectBloom\r\ncallproc PostEffectBloom\r\n')


def _static_manifest_for(assets):
    """Build a static.manifest: rows of (name, type, name hash, asset size)."""
    encoded = b''.join(name + b'\0' for name, _, _, _ in assets)
    manifest = bytearray(52 + 48 * len(assets) + len(encoded))
    struct.pack_into('<I', manifest, 16, len(assets))
    struct.pack_into('<4I', manifest, 36, 0, 0, len(encoded), 0)
    name_offset = 0
    for index, (_, type_code, name_hash, asset_size) in enumerate(assets):
        struct.pack_into('<12I', manifest, 52 + 48 * index, type_code, name_hash,
                         0, 0, 0, 0, name_offset, 0, asset_size, 0, 0, 0)
        name_offset += len(assets[index][0]) + 1
    manifest[52 + 48 * len(assets):] = encoded
    return bytes(manifest)


def _static_manifest(asset_size):
    return _static_manifest_for([(compat.GLOW_ASSET, compat.GLOW_TYPE,
                                  compat.GLOW_NAME_HASH, asset_size)])


def _light_asset():
    asset = bytearray(compat.LIGHT_ASSET_SIZE)
    struct.pack_into('<2f', asset, 0x3c, 1.0, 1.0)
    struct.pack_into('<2f', asset, 0x48, 70.0, 70.0)
    struct.pack_into('<2f', asset, 0x60, 1.0, 1.0)
    struct.pack_into('<2f', asset, 0x6c, 1.0, 1.0)
    return bytes(asset)


def _glow_asset():
    asset = bytearray(compat.GLOW_ASSET_SIZE)
    struct.pack_into('<2f', asset, 0x3c, 15.0, 15.0)
    struct.pack_into('<2f', asset, 0x48, 70.0, 70.0)
    struct.pack_into('<2f', asset, 0x60, 15.0, 15.0)
    struct.pack_into('<2f', asset, 0x6c, 1.0, 1.0)
    asset[0x80:0x80 + len(b'GPUParticle.fx\0')] = b'GPUParticle.fx\0'
    return bytes(asset)


if __name__ == '__main__':
    unittest.main(verbosity=2)
