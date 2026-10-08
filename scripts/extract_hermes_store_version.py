#!/usr/bin/env python3
"""Read the one row of the table ``schema_version`` of the Hermes store ``state.db``.

Hermes declares the schema version of its store in the table
``schema_version`` (one integer). That row names no session, so the row export
of a capture does not hold it. Without it the two version metrics of the
Hermes row stay unresolved. This script reads that one row and nothing else
from the session tables. It needs the owner's permission for a rows-only read
of ``state.db``; without ``--execute`` it only prints what it would do.

How it reads: the store and its ``-wal``, ``-shm`` and ``-journal`` files are
copied as bytes to a new temporary directory inside the output directory. The
original is never opened with SQLite. The copy is opened read-only. The script
reads the schema of the store (pragmas and ``sqlite_master``; no row) and the
rows of ``schema_version``. With ``--state-meta-keys`` it also lists the key
names of ``state_meta`` with the type and length of each value; no value is
written. The copy is then deleted and checked gone.

The result is ``state-db-schema-version.json`` in the new output directory.
``scripts/build_hermes_score_replays.py --version-extract`` binds it into each
packet. It is accepted only when its schema digest equals the schema digest
of the capture, and the decoder accepts only the values of
``SUPPORTED_SCHEMA_VERSIONS`` in ``session_bench/hermes_store_rows.py``.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.hermes_state_evidence import SIDECAR_SUFFIXES, _read_schema
from session_bench.hermes_store_rows import STORE, VERSION_EXTRACT_SCHEMA
from session_bench.native_replay import canonical

NAME = 'state-db-schema-version.json'


def _copy(source, target):
    """Copy one file as bytes; it must not change during the copy."""
    before = source.lstat()
    with open(source, 'rb') as stream:
        data = stream.read()
    after = source.lstat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or len(data) != before.st_size:
        raise ValueError(f'{source.name} changed during the copy; run again when Hermes is not running')
    target.write_bytes(data)


def extract(home, output, *, state_meta_keys=False):
    """Write the version extract of the store under ``home`` to the new directory ``output``. Returns the document."""
    home, output = Path(home), Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new output directory required')
    store = home / STORE
    if store.is_symlink() or not store.is_file():
        raise ValueError('the Hermes store is missing')
    output.mkdir(parents=True)
    temporary = output / '.store-copy'
    try:
        temporary.mkdir(mode=0o700)
        for name in [STORE] + [STORE + suffix for suffix in SIDECAR_SUFFIXES if (home / (STORE + suffix)).is_file()]:
            _copy(home / name, temporary / name)
        connection = sqlite3.connect('file:' + quote(str((temporary / STORE).absolute())) + '?mode=ro', uri=True)
        try:
            connection.execute('PRAGMA query_only=ON')
            schema, digest = _read_schema(connection)
            columns = [item[0] for item in connection.execute('SELECT * FROM schema_version LIMIT 0').description]
            rows = connection.execute('SELECT * FROM schema_version').fetchall()
            if columns != ['version'] or len(rows) != 1 or type(rows[0][0]) is not int:
                raise ValueError('schema_version is not one integer row')
            document = {'schema_version': VERSION_EXTRACT_SCHEMA, 'store': STORE, 'table': 'schema_version', 'rows': [{'version': rows[0][0]}],
                        'application_id': schema['application_id'], 'user_version': schema['user_version'], 'schema_sha256': digest,
                        'read': 'private temporary copy only, read-only; the copy was deleted; no row of a session table was read'}
            if state_meta_keys:
                document['state_meta_keys'] = [{'key': key, 'value_type': kind, 'value_length': size} for key, kind, size in connection.execute(
                    'SELECT key, typeof(value), length(value) FROM state_meta ORDER BY key')]
        finally:
            connection.close()
    except BaseException:
        shutil.rmtree(output, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    if temporary.exists():
        raise ValueError('the temporary copy of the store could not be deleted')
    (output / NAME).write_bytes(canonical(document) + b'\n')
    return document


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='new directory, for example artifacts/v1-expanded-preparation/hermes-store-version-extract-v1')
    parser.add_argument('--state-meta-keys', action='store_true', help='also list the key names of state_meta (no values)')
    parser.add_argument('--execute', action='store_true', help='read the store; needs the owner\'s permission')
    arguments = parser.parse_args()
    if not arguments.execute:
        print('Nothing was read. With --execute this copies ~/.hermes/state.db to a temporary directory, reads the one row of '
              'schema_version from the copy, deletes the copy and writes ' + str(arguments.output / NAME))
        raise SystemExit(0)
    result = extract(Path.home() / '.hermes', arguments.output, state_meta_keys=arguments.state_meta_keys)
    print(json.dumps({'output': str(arguments.output / NAME), 'schema_version': result['rows'][0]['version'],
                      'sha256': hashlib.sha256((arguments.output / NAME).read_bytes()).hexdigest()}))
