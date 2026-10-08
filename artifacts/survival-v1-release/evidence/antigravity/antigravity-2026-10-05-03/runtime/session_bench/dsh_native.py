"""Bounded DSH v4 physical JSONL reader, including concatenated Zstd frames.

Frame rules follow installed DSH persistence and AS's reference reader. This
module reads only explicitly selected bytes, never a vendor home or credentials.
"""
from __future__ import annotations
import ctypes
import ctypes.util
import hashlib
import os
from pathlib import Path

LIMIT = 128 * 1024 * 1024
FRAME_LIMIT = 100_000
MAGIC = b'\x28\xb5\x2f\xfd'


class DSHPhysicalError(ValueError):
    pass


def decompress_frames(data: bytes) -> tuple[bytes, list[dict]]:
    if not data or len(data) > LIMIT:
        raise DSHPhysicalError('compressed byte limit or empty file')
    library = ctypes.util.find_library('zstd')
    if not library:
        raise DSHPhysicalError('libzstd is unavailable')
    z = ctypes.CDLL(library)
    z.ZSTD_findFrameCompressedSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    z.ZSTD_findFrameCompressedSize.restype = ctypes.c_size_t
    z.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    z.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
    z.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
    z.ZSTD_decompress.restype = ctypes.c_size_t
    z.ZSTD_isError.argtypes = [ctypes.c_size_t]
    z.ZSTD_isError.restype = ctypes.c_uint
    offset = total = 0
    chunks, frames = [], []
    while offset < len(data):
        if len(frames) >= FRAME_LIMIT or data[offset:offset+4] != MAGIC:
            raise DSHPhysicalError('invalid frame boundary or excessive frame count')
        source = ctypes.create_string_buffer(data[offset:])
        size = z.ZSTD_findFrameCompressedSize(source, len(data) - offset)
        if z.ZSTD_isError(size) or size < 4 or size > len(data) - offset:
            raise DSHPhysicalError('corrupt or incomplete Zstd frame')
        expected = z.ZSTD_getFrameContentSize(source, size)
        if expected == 2**64 - 2:
            raise DSHPhysicalError('invalid Zstd frame content size')
        capacity = min(LIMIT - total, max(1, size * 1000))
        if expected != 2**64 - 1:
            if expected > capacity:
                raise DSHPhysicalError('decoded expansion exceeds limit')
            capacity = max(1, expected)
        destination = ctypes.create_string_buffer(capacity)
        produced = z.ZSTD_decompress(destination, capacity, source, size)
        if z.ZSTD_isError(produced) or total + produced > LIMIT:
            raise DSHPhysicalError('Zstd checksum, expansion or decompression failure')
        chunk = destination.raw[:produced]
        if not chunk or not chunk.endswith(b'\n'):
            raise DSHPhysicalError('frame must contain complete nonempty JSONL records')
        if not frames and len(chunk.splitlines()) != 1:
            raise DSHPhysicalError('first frame must contain only the header')
        frames.append({'index': len(frames), 'compressed_offset': offset, 'compressed_length': size,
                       'decoded_length': produced, 'decoded_sha256': hashlib.sha256(chunk).hexdigest()})
        chunks.append(chunk)
        total += produced
        offset += size
    return b''.join(chunks), frames


def read_physical(path: Path) -> tuple[bytes, dict]:
    from .adapters.deepseek_harness import _read_bounded_regular_file
    path = Path(path)
    if Path(os.path.abspath(path)) != path.resolve():
        raise DSHPhysicalError('native path traverses a symlink')
    data = _read_bounded_regular_file(path)
    if path.name == 'session.v4.jsonl.zstd':
        decoded, frames = decompress_frames(data)
    elif path.name == 'session.v4.jsonl':
        decoded, frames = data, []
    else:
        raise DSHPhysicalError('unsupported native generation filename')
    return decoded, {'physical_sha256': hashlib.sha256(data).hexdigest(),
                     'logical_sha256': hashlib.sha256(decoded).hexdigest(),
                     'physical_bytes': len(data), 'logical_bytes': len(decoded), 'frames': frames}
