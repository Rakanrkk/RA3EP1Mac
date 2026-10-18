#!/usr/bin/env python3
r"""Build pinned, source-only RA3 compatibility archive copies.

Example:
  python3 -I packaging/resources/compat_resources.py \
      --game-dir /path/to/Red\ Alert\ 3\ Uprising \
      --output-dir /path/to/new/compat-resources

The input is read from GAME_DIR/Data and never modified. OUTPUT_DIR must not
exist. Existing accepted archives are copied; known originals are transformed
and verified against their accepted output digests before publication.
"""

from dataclasses import dataclass
import argparse
import filecmp
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile

BLOOM_TARGET = 'shaders/compiled/ingame.scrapeo'
STATIC_MANIFEST = 'data/static.manifest'
STATIC_BINARY = 'data/static.bin'
GLOW_ASSET = b'FXParticleSystemTemplate:SovietPowerGlow'
GLOW_TYPE = 0x28e7fd7f
GLOW_NAME_HASH = 0xbd8cd4c6
GLOW_ASSET_SIZE = 552
MAX_REFPACK_SIZE = 500_000_000
COPY_BUFFER_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveContract:
    name: str
    original_sha256: str
    patched_sha256: str


CONTRACTS = (
    ArchiveContract('Shaders.big',
                    '165432f89a78a84000c1389cd8d8c2c47ce9cc72fdb15c2c8c845d8c74388df4',
                    '76c371082a2df0ff69a919e0a196d7869f8a206736b8f484fa9155abbee2f651'),
    ArchiveContract('Misc.big',
                    '6fa2e6fd42b0550aefd594a08cce3250a4790824d8daf6bc8177a62c798d8ea9',
                    'b353c53388f8798a903498248472277b1947b9141d44230e622152f085cf89a2'),
    ArchiveContract('StaticStream.big',
                    '6e1b47a22db7f66ad5d1a2c01f82139599a30975bc78502ef44403670643dc03',
                    '0daeb2d3aedfe7a83d75dc61732af8232de3ffb13603882aa5e216f0b9853834'),
)


class ResourceError(RuntimeError):
    """The input or generated archive did not meet the pinned contract."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(COPY_BUFFER_SIZE), b''):
            digest.update(block)
    return digest.hexdigest()


def _read_exact(stream, size, what):
    data = stream.read(size)
    if len(data) != size:
        raise ResourceError('truncated %s' % what)
    return data


def _parse_big_index(index, file_size):
    """Parse an index prefix with the total archive size available separately."""
    if len(index) < 16 or index[:4] not in (b'BIG4', b'BIGF'):
        raise ResourceError('not a supported BIG4/BIGF archive')
    count, index_end = struct.unpack_from('>II', index, 8)
    if index_end < 16 or index_end > len(index) or index_end > file_size:
        raise ResourceError('BIG index end is outside the archive')
    position = 16
    entries = []
    names = set()
    for _ in range(count):
        if position + 8 > index_end:
            raise ResourceError('truncated BIG index entry')
        offset, size = struct.unpack_from('>II', index, position)
        index_position = position
        position += 8
        stop = index.find(b'\0', position, index_end)
        if stop < 0:
            raise ResourceError('unterminated BIG member name')
        try:
            name = index[position:stop].decode('ascii').replace('\\', '/')
        except UnicodeDecodeError as error:
            raise ResourceError('non-ASCII BIG member name') from error
        member_path = PurePosixPath(name)
        if (not name or member_path.is_absolute() or
                any(part in ('', '.', '..') for part in name.split('/'))):
            raise ResourceError('unsafe BIG member name %r' % name)
        if name in names:
            raise ResourceError('duplicate BIG member %r' % name)
        names.add(name)
        position = stop + 1
        if offset < index_end or offset + size > file_size:
            raise ResourceError('BIG member range is outside archive: %s' % name)
        entries.append((name, offset, size, index_position))
    if position > index_end:
        raise ResourceError('BIG index exceeds its declared end')
    return entries


def big_catalog_bytes(raw):
    """Parse a BIG4/BIGF index into names and source byte ranges."""
    return _parse_big_index(raw, len(raw))


def big_catalog_file(path):
    """Read only the header and index from a BIG file, retaining member ranges."""
    with Path(path).open('rb') as stream:
        header = _read_exact(stream, 16, 'BIG header')
        if header[:4] not in (b'BIG4', b'BIGF'):
            raise ResourceError('not a supported BIG4/BIGF archive')
        count, index_end = struct.unpack_from('>II', header, 8)
        stream.seek(0, os.SEEK_END)
        file_size = stream.tell()
        if index_end < 16 or index_end > file_size:
            raise ResourceError('BIG index end is outside the archive')
        stream.seek(0)
        index = _read_exact(stream, index_end, 'BIG index')
    return _parse_big_index(index, file_size)


def refpack_unpack(data, max_size=MAX_REFPACK_SIZE):
    """Decode EA 0x10FB/0x90FB RefPack bytes, or return an uncompressed entry."""
    data = bytes(data)
    if len(data) < 2 or data[:2] not in (b'\x10\xfb', b'\x90\xfb'):
        return data
    width = 3 if data[0] == 0x10 else 4
    header_size = 2 + width
    if len(data) < header_size:
        raise ResourceError('truncated RefPack header')
    expected = int.from_bytes(data[2:header_size], 'big')
    if not 0 < expected <= max_size:
        raise ResourceError('RefPack output size outside limit: %d' % expected)
    position = header_size
    output = bytearray()

    def need(size, label):
        if position + size > len(data):
            raise ResourceError('truncated RefPack %s' % label)

    while True:
        need(1, 'command')
        code = data[position]
        position += 1
        count = distance = 0
        if code < 0x80:
            need(1, 'short match')
            byte = data[position]
            position += 1
            literal = code & 3
            count = ((code >> 2) & 7) + 3
            distance = ((code & 0x60) << 3) + byte + 1
        elif code < 0xc0:
            need(2, 'medium match')
            first, second = data[position:position + 2]
            position += 2
            literal = first >> 6
            count = (code & 0x3f) + 4
            distance = ((first & 0x3f) << 8) + second + 1
        elif code < 0xe0:
            need(3, 'long match')
            first, second, third = data[position:position + 3]
            position += 3
            literal = code & 3
            count = ((code & 0x0c) << 6) + third + 5
            distance = ((code & 0x10) << 12) + (first << 8) + second + 1
        else:
            literal = ((code & 0x1f) << 2) + 4 if code < 0xfc else code & 3
        need(literal, 'literal run')
        output.extend(data[position:position + literal])
        position += literal
        if count:
            if not 0 < distance <= len(output):
                raise ResourceError('invalid RefPack back-reference')
            for _ in range(count):
                output.append(output[-distance])
        if len(output) > expected:
            raise ResourceError('RefPack output exceeds declared length')
        if code >= 0xfc:
            break
    if len(output) != expected or position != len(data):
        raise ResourceError('RefPack stream length mismatch')
    return bytes(output)


def _refpack_stream_header(stream, offset, size, max_size):
    """Read RefPack's length and command-stream start at an archive range."""
    stream.seek(offset)
    header = _read_exact(stream, min(size, 6), 'RefPack header')
    if len(header) < 2 or header[:2] not in (b'\x10\xfb', b'\x90\xfb'):
        raise ResourceError('static binary stream is not RefPack encoded')
    width = 3 if header[0] == 0x10 else 4
    header_size = 2 + width
    if len(header) < header_size:
        raise ResourceError('truncated RefPack header')
    expected = int.from_bytes(header[2:header_size], 'big')
    if not 0 < expected <= max_size:
        raise ResourceError('RefPack output size outside limit: %d' % expected)
    return expected, header_size


def decode_refpack_range(archive_path, offset, size, output_path,
                         stop_at=None, max_size=MAX_REFPACK_SIZE):
    """Decode one archived RefPack member to disk and optionally capture a token boundary."""
    with Path(archive_path).open('rb') as source:
        expected, position = _refpack_stream_header(source, offset, size, max_size)
        end = offset + size
        output_path = Path(output_path)
        boundary = None
        output_size = 0
        with output_path.open('wb+') as decoded:
            while True:
                token_start = position
                if offset + position >= end:
                    raise ResourceError('truncated RefPack command')
                source.seek(offset + position)
                code = _read_exact(source, 1, 'RefPack command')[0]
                position += 1
                count = distance = 0
                if code < 0x80:
                    first = _read_range(source, offset, end, position, 1, 'short match')[0]
                    position += 1
                    literal = code & 3
                    count = ((code >> 2) & 7) + 3
                    distance = ((code & 0x60) << 3) + first + 1
                elif code < 0xc0:
                    first, second = _read_range(source, offset, end, position, 2, 'medium match')
                    position += 2
                    literal = first >> 6
                    count = (code & 0x3f) + 4
                    distance = ((first & 0x3f) << 8) + second + 1
                elif code < 0xe0:
                    first, second, third = _read_range(source, offset, end, position, 3, 'long match')
                    position += 3
                    literal = code & 3
                    count = ((code & 0x0c) << 6) + third + 5
                    distance = ((code & 0x10) << 12) + (first << 8) + second + 1
                else:
                    literal = ((code & 0x1f) << 2) + 4 if code < 0xfc else code & 3
                literal_data = _read_range(source, offset, end, position, literal, 'literal run')
                decoded.write(literal_data)
                position += literal
                output_size += literal
                if count:
                    if not 0 < distance <= output_size:
                        raise ResourceError('invalid RefPack back-reference')
                    remaining = count
                    while remaining:
                        take = min(remaining, distance, COPY_BUFFER_SIZE)
                        decoded.seek(output_size - distance)
                        chunk = _read_exact(decoded, take, 'decoded back-reference')
                        decoded.seek(output_size)
                        decoded.write(chunk)
                        output_size += take
                        remaining -= take
                if output_size > expected:
                    raise ResourceError('RefPack output exceeds declared length')
                token_output_start = output_size - literal - count
                if stop_at is not None and boundary is None and token_output_start <= stop_at < output_size:
                    boundary = (token_start, token_output_start)
                if code >= 0xfc:
                    break
            if output_size != expected or position != size:
                raise ResourceError('RefPack stream length mismatch')
            decoded.flush()
    if stop_at is not None and boundary is None:
        raise ResourceError('requested decoded offset is outside the RefPack stream')
    return {'decoded_size': expected, 'boundary': boundary}


def _read_range(stream, base, end, position, size, label):
    if position + size > end - base:
        raise ResourceError('truncated RefPack %s' % label)
    stream.seek(base + position)
    return _read_exact(stream, size, 'RefPack ' + label)


def encode_literal_suffix(source_path, start, length, destination):
    """Encode a literal-only RefPack suffix using the accepted token layout."""
    source_path = Path(source_path)
    remaining = length
    position = start
    with source_path.open('rb') as source, Path(destination).open('wb') as target:
        while remaining >= 4:
            count = min((remaining // 4) * 4, 112)
            target.write(bytes((0xe0 + (count - 4) // 4,)))
            source.seek(position)
            shutil.copyfileobj(_LimitedReader(source, count), target, COPY_BUFFER_SIZE)
            position += count
            remaining -= count
        target.write(bytes((0xfc + remaining,)))
        if remaining:
            source.seek(position)
            target.write(_read_exact(source, remaining, 'literal suffix'))


class _LimitedReader:
    """File-like view that yields at most a fixed number of bytes."""

    def __init__(self, stream, remaining):
        self.stream = stream
        self.remaining = remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b''
        if size < 0 or size > self.remaining:
            size = self.remaining
        data = self.stream.read(size)
        self.remaining -= len(data)
        return data


def replace_big_entry_bytes(raw, name, payload):
    """Append an uncompressed member and repoint one BIG index row."""
    entries = big_catalog_bytes(raw)
    hits = [entry for entry in entries if entry[0] == name]
    if len(hits) != 1:
        raise ResourceError('expected one BIG member %s, found %d' % (name, len(hits)))
    if len(raw) + len(payload) > 0xffffffff:
        raise ResourceError('BIG output exceeds its 32-bit length field')
    output = bytearray(raw)
    _, _, _, index_position = hits[0]
    struct.pack_into('>II', output, index_position, len(raw), len(payload))
    output.extend(payload)
    struct.pack_into('<I', output, 4, len(output))
    return bytes(output)


def replace_big_entry_file(source_path, name, payload_path, output_path):
    """Append a staged member and repoint its BIG row without loading the archive."""
    source_path, payload_path, output_path = map(Path, (source_path, payload_path, output_path))
    entries = big_catalog_file(source_path)
    hits = [entry for entry in entries if entry[0] == name]
    if len(hits) != 1:
        raise ResourceError('expected one BIG member %s, found %d' % (name, len(hits)))
    source_size = source_path.stat().st_size
    payload_size = payload_path.stat().st_size
    if source_size + payload_size > 0xffffffff:
        raise ResourceError('BIG output exceeds its 32-bit length field')
    _, _, _, index_position = hits[0]
    with source_path.open('rb') as source, output_path.open('xb+') as output:
        shutil.copyfileobj(source, output, COPY_BUFFER_SIZE)
        output.seek(index_position)
        output.write(struct.pack('>II', source_size, payload_size))
        output.seek(0, os.SEEK_END)
        with payload_path.open('rb') as payload:
            shutil.copyfileobj(payload, output, COPY_BUFFER_SIZE)
        output.seek(4)
        output.write(struct.pack('<I', source_size + payload_size))
        output.flush()
        os.fsync(output.fileno())


def _archive_member(raw, entries, name):
    hits = [entry for entry in entries if entry[0] == name]
    if len(hits) != 1:
        raise ResourceError('expected one BIG member %s, found %d' % (name, len(hits)))
    _, offset, size, _ = hits[0]
    return refpack_unpack(raw[offset:offset + size])


def _patch_bloom_script(script, expected_hash):
    """Change only the six authored Bloom texture-format fields."""
    if sha256_bytes(script) != expected_hash:
        raise ResourceError('Bloom script digest does not match the pinned source')
    pattern = (rb'(?m)^([ \t]*var texture BloomTexture(?:High|Medium|Low)[01] [^\r\n]+)'
               rb'\$PostEffect\.Bloom\.TextureFormat\r$')
    patched, changed = re.subn(pattern, rb'\1A8R8G8B8\r', script)
    if changed != 6:
        raise ResourceError('expected six Bloom texture-format fields, found %d' % changed)
    if patched.count(b'callproc PostEffectBloom') != 2:
        raise ResourceError('Bloom post-effect calls changed unexpectedly')
    reverse = (rb'(?m)^([ \t]*var texture BloomTexture(?:High|Medium|Low)[01] [^\r\n]+)'
               rb'A8R8G8B8\r$')
    if re.sub(reverse, rb'\1$PostEffect.Bloom.TextureFormat\r', patched) != script:
        raise ResourceError('Bloom script changed outside the six texture-format values')
    return patched


def patch_bloom_script(script):
    """Patch the pinned original Bloom script used by the public source archives."""
    return _patch_bloom_script(
        script, 'e8deec02f430f683c73d87554616e60aaf31866de08a33b2bcbeeeb792b207ce')


def _restore_bloom_lines(script):
    reverse = (rb'(?m)^([ \t]*var texture BloomTexture(?:High|Medium|Low)[01] [^\r\n]+)'
               rb'A8R8G8B8\r$')
    return re.sub(reverse, lambda match: match.group(1) +
                  b'$PostEffect.Bloom.TextureFormat\r', script)


def _patch_bloom_archive(raw, expected_script_hash):
    """Patch a known-original shader BIG and validate every member."""
    entries = big_catalog_bytes(raw)
    original = _archive_member(raw, entries, BLOOM_TARGET)
    patched_script = _patch_bloom_script(original, expected_script_hash)
    candidate = replace_big_entry_bytes(raw, BLOOM_TARGET, patched_script)
    updated = big_catalog_bytes(candidate)
    if [entry[0] for entry in updated] != [entry[0] for entry in entries]:
        raise ResourceError('BIG member inventory changed during Bloom patch')
    for entry in entries:
        name = entry[0]
        if name == BLOOM_TARGET:
            continue
        if _archive_member(raw, entries, name) != _archive_member(candidate, updated, name):
            raise ResourceError('unrelated BIG member changed: %s' % name)
    if _archive_member(candidate, updated, BLOOM_TARGET) != patched_script:
        raise ResourceError('Bloom target does not read back exactly')
    return candidate


def _parse_static_manifest(manifest):
    if len(manifest) < 52:
        raise ResourceError('static manifest header is truncated')
    count = struct.unpack_from('<I', manifest, 16)[0]
    refs, streams, names_size, sources_size = struct.unpack_from('<4I', manifest, 36)
    name_base = 52 + 48 * count + refs + streams
    if name_base + names_size + sources_size != len(manifest):
        raise ResourceError('static manifest section sizes do not match')
    needle = GLOW_ASSET + b'\0'
    name_hits = []
    start = name_base
    stop = name_base + names_size
    while True:
        found = manifest.find(needle, start, stop)
        if found < 0:
            break
        name_hits.append(found - name_base)
        start = found + 1
    if len(name_hits) != 1:
        raise ResourceError('expected one SovietPowerGlow manifest name')
    name_offset = name_hits[0]
    binary_offset = 8
    asset_hits = []
    for index in range(count):
        row_offset = 52 + 48 * index
        fields = struct.unpack_from('<12I', manifest, row_offset)
        if fields[6] == name_offset:
            asset_hits.append((index, fields, binary_offset))
        binary_offset += fields[8]
    if len(asset_hits) != 1:
        raise ResourceError('expected one SovietPowerGlow manifest asset')
    index, fields, asset_offset = asset_hits[0]
    if fields[0] != GLOW_TYPE or fields[1] != GLOW_NAME_HASH or fields[8] != GLOW_ASSET_SIZE:
        raise ResourceError('SovietPowerGlow type, name hash or size differs from the pin')
    return {'asset_index': index, 'asset_offset': asset_offset,
            'asset_size': fields[8], 'decoded_size': binary_offset}


def patch_static_archive(source_path, output_path, temporary_directory):
    """Patch only SovietPowerGlow burst intervals in one pinned StaticStream BIG."""
    source_path, output_path = Path(source_path), Path(output_path)
    temporary_directory = Path(temporary_directory)
    entries = big_catalog_file(source_path)
    by_name = {entry[0]: entry for entry in entries}
    if STATIC_MANIFEST not in by_name or STATIC_BINARY not in by_name:
        raise ResourceError('StaticStream archive lacks required manifest or binary members')

    manifest_entry = by_name[STATIC_MANIFEST]
    with source_path.open('rb') as source:
        source.seek(manifest_entry[1])
        manifest_packed = _read_exact(source, manifest_entry[2], STATIC_MANIFEST)
    manifest = refpack_unpack(manifest_packed)
    record = _parse_static_manifest(manifest)

    stream_entry = by_name[STATIC_BINARY]
    decoded_original = temporary_directory / 'static-stream.original.decoded'
    decoded_patched = temporary_directory / 'static-stream.patched.decoded'
    packed_patched = temporary_directory / 'static-stream.patched.refpack'
    target_start = record['asset_offset']
    cutoff = target_start + 0x60
    decoded = decode_refpack_range(
        source_path, stream_entry[1], stream_entry[2], decoded_original,
        stop_at=cutoff)
    if decoded['decoded_size'] != record['decoded_size']:
        raise ResourceError('static manifest binary length disagrees with decoded stream')
    token_start, output_start = decoded['boundary']
    with decoded_original.open('rb') as source:
        source.seek(target_start)
        asset = _read_exact(source, record['asset_size'], 'SovietPowerGlow asset')
    if struct.unpack_from('<2f', asset, 0x3c) != (15.0, 15.0):
        raise ResourceError('SovietPowerGlow lifetime differs from expected 15/15')
    if struct.unpack_from('<2f', asset, 0x48) != (70.0, 70.0):
        raise ResourceError('SovietPowerGlow size differs from expected 70/70')
    if struct.unpack_from('<2f', asset, 0x60) != (15.0, 15.0):
        raise ResourceError('SovietPowerGlow burst interval differs from expected 15/15')
    if struct.unpack_from('<2f', asset, 0x6c) != (1.0, 1.0):
        raise ResourceError('SovietPowerGlow burst count differs from expected 1/1')
    if b'GPUParticle.fx\0' not in asset:
        raise ResourceError('SovietPowerGlow particle effect marker is missing')

    shutil.copyfile(decoded_original, decoded_patched)
    with decoded_patched.open('r+b') as patched:
        patched.seek(target_start + 0x60)
        patched.write(struct.pack('<2f', 14.0, 14.0))
        patched.flush()
        os.fsync(patched.fileno())

    # Preserve every packed token before the token that emits byte 0x60, then
    # encode the remaining decoded suffix as literals, matching the accepted patch.
    _, header_size = _refpack_stream_header_for_path(
        source_path, stream_entry[1], stream_entry[2])
    with source_path.open('rb') as source, packed_patched.open('xb') as packed:
        source.seek(stream_entry[1])
        prefix = _read_exact(source, token_start, 'RefPack token prefix')
        if len(prefix) < header_size:
            raise ResourceError('RefPack token boundary precedes the header')
        packed.write(prefix)
    encode_literal_suffix_file = encode_literal_suffix
    suffix_temp = temporary_directory / 'static-stream.literal-suffix'
    encode_literal_suffix_file(decoded_patched, output_start,
                               decoded['decoded_size'] - output_start, suffix_temp)
    with suffix_temp.open('rb') as suffix, packed_patched.open('ab') as packed:
        shutil.copyfileobj(suffix, packed, COPY_BUFFER_SIZE)
        packed.flush()
        os.fsync(packed.fileno())

    decoded_check = temporary_directory / 'static-stream.patched-check.decoded'
    # Decode the newly written RefPack stream from its temporary file through a
    # compact synthetic BIG wrapper so exact decoded bytes can be compared.
    wrapped = temporary_directory / 'static-stream.refpack-check.big'
    _write_single_member_big(packed_patched, wrapped, STATIC_BINARY)
    check_entry = big_catalog_file(wrapped)[0]
    decode_refpack_range(wrapped, check_entry[1], check_entry[2], decoded_check)
    if not filecmp.cmp(decoded_patched, decoded_check, shallow=False):
        raise ResourceError('rewritten RefPack stream does not decode to the expected bytes')

    replace_big_entry_file(source_path, STATIC_BINARY, packed_patched, output_path)
    patched_entries = big_catalog_file(output_path)
    if [entry[0] for entry in patched_entries] != [entry[0] for entry in entries]:
        raise ResourceError('StaticStream BIG member inventory changed')
    output_ranges = {entry[0]: (entry[1], entry[2]) for entry in patched_entries}
    for name, offset, size, _ in entries:
        if name == STATIC_BINARY:
            continue
        other_offset, other_size = output_ranges[name]
        if (size != other_size or
                not _file_ranges_equal(source_path, offset, output_path, other_offset, size)):
            raise ResourceError('unrelated StaticStream member changed: %s' % name)
    return {'asset_index': record['asset_index'], 'changed_asset_offsets': ['0x60', '0x64'],
            'decoded_stream_size': decoded['decoded_size'], 'verified_other_entries': len(entries) - 1}


def _refpack_stream_header_for_path(path, offset, size):
    with Path(path).open('rb') as source:
        expected, header_size = _refpack_stream_header(source, offset, size, MAX_REFPACK_SIZE)
    return expected, header_size


def _write_single_member_big(payload_path, destination, member_name):
    """Create a minimal BIG archive used only for validating a staged RefPack stream."""
    payload_path, destination = Path(payload_path), Path(destination)
    name = member_name.encode('ascii') + b'\0'
    index_end = 16 + 8 + len(name)
    payload_size = payload_path.stat().st_size
    total_size = index_end + payload_size
    if total_size > 0xffffffff:
        raise ResourceError('temporary BIG exceeds the 32-bit size limit')
    with destination.open('xb+') as output:
        output.write(b'BIG4')
        output.write(struct.pack('<I', total_size))
        output.write(struct.pack('>II', 1, index_end))
        output.write(struct.pack('>II', index_end, payload_size))
        output.write(name)
        with payload_path.open('rb') as payload:
            shutil.copyfileobj(payload, output, COPY_BUFFER_SIZE)


def _file_ranges_equal(left_path, left_offset, right_path, right_offset, size):
    with Path(left_path).open('rb') as left, Path(right_path).open('rb') as right:
        left.seek(left_offset)
        right.seek(right_offset)
        remaining = size
        while remaining:
            count = min(remaining, COPY_BUFFER_SIZE)
            if left.read(count) != right.read(count):
                return False
            if right.read(0) != b'':
                raise AssertionError('unreachable')
            remaining -= count
    return True


def _copy_verified(source, destination, expected_hash):
    with Path(source).open('rb') as src, Path(destination).open('xb') as dst:
        shutil.copyfileobj(src, dst, COPY_BUFFER_SIZE)
        dst.flush()
        os.fsync(dst.fileno())
    actual = sha256_file(destination)
    if actual != expected_hash:
        raise ResourceError('output hash mismatch: expected %s, got %s' % (expected_hash, actual))
    return actual


def generate_resources(game_dir, output_dir, contracts=CONTRACTS):
    """Build exactly three verified archive copies inside a previously absent directory."""
    game_dir, output_dir = Path(game_dir).expanduser(), Path(output_dir).expanduser()
    if os.path.lexists(output_dir):
        raise ResourceError('output directory already exists; refusing to overwrite')
    contract_map = {contract.name: contract for contract in contracts}
    expected_names = {contract.name for contract in CONTRACTS}
    if set(contract_map) != expected_names:
        raise ResourceError('archive contract set must contain exactly the three pinned archives')
    sources = {name: game_dir / 'Data' / name for name in expected_names}
    input_hashes = {}
    states = {}
    for name in ('Shaders.big', 'Misc.big', 'StaticStream.big'):
        source = sources[name]
        if not source.is_file():
            raise ResourceError('required source archive is missing: Data/%s' % name)
        actual = sha256_file(source)
        contract = contract_map[name]
        if actual == contract.original_sha256:
            state = 'original'
        elif actual == contract.patched_sha256:
            state = 'already_patched'
        else:
            raise ResourceError('unknown %s input hash %s' % (name, actual))
        input_hashes[name] = actual
        states[name] = state

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(output_dir):
        raise ResourceError('output directory appeared during validation; refusing to overwrite')
    staging_parent = output_dir.parent
    staging = Path(tempfile.mkdtemp(prefix='.' + output_dir.name + '.stage-', dir=staging_parent))
    staging_work = staging / '.work'
    staging_work.mkdir()
    reports = []
    try:
        for name in ('Shaders.big', 'Misc.big', 'StaticStream.big'):
            contract = contract_map[name]
            source = sources[name]
            candidate = staging / name
            details = {}
            if states[name] == 'already_patched':
                actual = _copy_verified(source, candidate, contract.patched_sha256)
                operation = 'copied_accepted_input'
            elif name in ('Shaders.big', 'Misc.big'):
                raw = source.read_bytes()
                patched = _patch_bloom_archive(raw, 'e8deec02f430f683c73d87554616e60aaf31866de08a33b2bcbeeeb792b207ce')
                with candidate.open('xb') as output:
                    output.write(patched)
                    output.flush()
                    os.fsync(output.fileno())
                actual = sha256_file(candidate)
                operation = 'patched_bloom_texture_formats'
            else:
                details = patch_static_archive(source, candidate, staging_work)
                actual = sha256_file(candidate)
                operation = 'patched_soviet_power_glow'
            if actual != contract.patched_sha256:
                raise ResourceError('%s output hash mismatch: expected %s, got %s' % (
                    name, contract.patched_sha256, actual))
            reports.append({'archive': name, 'input_sha256': input_hashes[name],
                            'input_state': states[name], 'output_sha256': actual,
                            'operation': operation, **details})
        shutil.rmtree(staging_work)
        # Claim the absent destination explicitly, then move only our three staged files.
        output_dir.mkdir(exist_ok=False)
        moved = []
        try:
            for name in ('Shaders.big', 'Misc.big', 'StaticStream.big'):
                destination = output_dir / name
                os.rename(staging / name, destination)
                moved.append(destination)
            staging.rmdir()
        except BaseException:
            for destination in moved:
                destination.unlink(missing_ok=True)
            output_dir.rmdir()
            raise
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        if output_dir.exists():
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise
    return {'status': 'PASS', 'output_dir': str(output_dir), 'archives': reports,
            'game_directory_modified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', required=True,
                        help='game root containing Data/Shaders.big, Misc.big and StaticStream.big')
    parser.add_argument('--output-dir', required=True,
                        help='new directory for the three patched archive copies; must not exist')
    args = parser.parse_args(argv)
    result = generate_resources(args.game_dir, args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ResourceError, ValueError, struct.error) as error:
        raise SystemExit('compat resource generation failed: %s' % error)
