#!/usr/bin/env python3
"""Write the next trusted index, status and partial-coverage files for one newly reviewed configuration."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def add(configuration, packets, review, index, statuses, coverage, out_index, out_statuses, out_coverage):
    packets, review = Path(packets), Path(review)
    bundle = packets / 'public-inputs-candidate.json'
    document = json.loads(review.read_bytes())
    if document['public_bundle_sha256'] != sha(bundle): raise ValueError('review does not bind this bundle')
    names = {sha(path / 'manifest.json'): path for path in packets.iterdir() if (path / 'manifest.json').is_file()}
    rows = []
    for run in document['runs']:
        if run['manifest_sha256'] not in names: raise ValueError('review names a manifest that is not in the packet set')
        rows.append({'manifest_sha256': run['manifest_sha256'], 'path': names[run['manifest_sha256']].as_posix(), 'run_id': run['run_id']})
    trusted = json.loads(Path(index).read_bytes())
    if any(row['configuration_id'] == configuration for row in trusted['configurations']): raise ValueError('configuration already trusted')
    trusted['configurations'].append({'configuration_id': configuration, 'packets': rows, 'producer_id': document['producer_id'],
                                      'public_inputs': bundle.as_posix(), 'review': review.as_posix(),
                                      'reviewer_id': document['reviewer_id'], 'trusted_review_sha256': sha(review)})
    status = json.loads(Path(statuses).read_bytes())
    for row in status if isinstance(status, list) else status['rows']:
        if row['configuration_id'] == configuration:
            row['attempt_ids'] = [item['run_id'] for item in rows]
            row['evidence_refs'] = [(packets / 'summary.json').as_posix(), bundle.as_posix()]
            row['reason_ids'] = ['ranking_requires_release_builder_verification']
    partial = json.loads(Path(coverage).read_bytes())
    partial['configurations'] = [row for row in partial['configurations'] if row['configuration_id'] != configuration]
    for path, value in ((out_index, trusted), (out_statuses, status), (out_coverage, partial)):
        with Path(path).open('x') as stream: stream.write(json.dumps(value, indent=1) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('configuration', 'packets', 'review', 'index', 'statuses', 'coverage', 'out-index', 'out-statuses', 'out-coverage'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    add(args.configuration, args.packets, args.review, args.index, args.statuses, args.coverage, args.out_index, args.out_statuses, args.out_coverage)
