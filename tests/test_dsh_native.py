import ctypes
import ctypes.util
import pytest

from session_bench.dsh_native import decompress_frames, DSHPhysicalError


def compress(value):
    library = ctypes.util.find_library('zstd')
    if library is None:
        pytest.skip('libzstd unavailable')
    z = ctypes.CDLL(library)
    z.ZSTD_compressBound.argtypes = [ctypes.c_size_t]
    z.ZSTD_compressBound.restype = ctypes.c_size_t
    z.ZSTD_compress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    z.ZSTD_compress.restype = ctypes.c_size_t
    capacity = z.ZSTD_compressBound(len(value))
    output = ctypes.create_string_buffer(capacity)
    size = z.ZSTD_compress(output, capacity, value, len(value), 3)
    return output.raw[:size]


def test_concatenated_frames_preserve_each_complete_record():
    chunks = [b'{"type":"session","version":4}\n', b'{"seq":0}\n{"seq":1}\n', b'{"seq":2}\n']
    data = b''.join(compress(x) for x in chunks)
    decoded, frames = decompress_frames(data)
    assert decoded == b''.join(chunks)
    assert len(frames) == 3
    assert sum(f['compressed_length'] for f in frames) == len(data)
    assert sum(f['decoded_length'] for f in frames) == len(decoded)


@pytest.mark.parametrize('transform', [lambda b:b[:-1], lambda b:b+b'x', lambda b:b'bad'+b, lambda b:b''])
def test_corruption_and_torn_tail_refused(transform):
    with pytest.raises(DSHPhysicalError):
        decompress_frames(transform(compress(b'{}\n')))


@pytest.mark.parametrize('chunks', [[b'{}\n{}\n'], [b'{}'], [b'{}\n', b'{"seq":0}']])
def test_physical_header_and_line_boundary_required(chunks):
    with pytest.raises(DSHPhysicalError):
        decompress_frames(b''.join(compress(x) for x in chunks))


def test_expansion_limit(monkeypatch):
    import session_bench.dsh_native as module
    data = compress(b'{}\n') + compress(b'x' * 2048 + b'\n')
    monkeypatch.setattr(module, 'LIMIT', 1024)
    with pytest.raises(DSHPhysicalError, match='expansion'):
        decompress_frames(data)
