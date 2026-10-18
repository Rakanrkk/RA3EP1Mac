"""Measure marker rows in an owned, non-interlaced RGBA PNG capture (stdlib only)."""
import argparse
import json
from pathlib import Path
import struct
import zlib


def center_rows(path):
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('Not PNG')
    offset = 8
    chunks = []
    width = height = 0
    while offset < len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset+4:offset+8]
        value = data[offset+8:offset+8+length]
        offset += length + 12
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', value)
            if (depth, color, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise RuntimeError('Expected non-interlaced RGBA8 PNG')
        elif kind == b'IDAT':
            chunks.append(value)
    raw = zlib.decompress(b''.join(chunks))
    stride = width * 4
    if len(raw) != height * (stride + 1):
        raise RuntimeError('PNG data length mismatch')
    previous = bytearray(stride)
    spans = []
    for y in range(height):
        pos = y * (stride+1)
        filter_type = raw[pos]
        row = bytearray(raw[pos+1:pos+1+stride])
        for x in range(stride):
            left = row[x-4] if x >= 4 else 0
            up = previous[x]
            upper_left = previous[x-4] if x >= 4 else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = up
            elif filter_type == 3:
                predictor = (left + up) // 2
            elif filter_type == 4:
                estimate = left + up - upper_left
                distances = (abs(estimate-left), abs(estimate-up), abs(estimate-upper_left))
                predictor = left if distances[0] <= min(distances[1:]) else up if distances[1] <= distances[2] else upper_left
            elif filter_type == 0:
                predictor = 0
            else:
                raise RuntimeError('Unsupported PNG filter')
            row[x] = (row[x] + predictor) & 255
        rgba = list(row[(width//2)*4:(width//2)*4+4])
        r, g, b, _ = rgba
        label = 'red' if r > 150 and g < 100 else 'green' if g > 150 and r < 100 else 'black' if max(r, g, b) < 20 else 'content'
        if not spans or spans[-1]['label'] != label:
            spans.append({'start_row': y, 'label': label, 'rgba': rgba})
        previous = row
    return {'width': width, 'height': height, 'center_column_spans': spans}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    print(json.dumps(center_rows(args.capture), indent=2))
