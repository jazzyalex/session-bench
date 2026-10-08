"""The hosted v1 table is a pure function of the release."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import export_v1_leaderboard as export


def test_data_file_equals_the_generated_text_and_ranks_twelve_rows():
    document = export.build()
    assert (ROOT / 'data/leaderboard-v1.yml').read_text() == export.render(document)
    assert [row['rank'] for row in document['agents']] == list(range(1, 13))
    for row in document['agents']:
        assert abs(sum(float(value) for value in row['categories'].values()) - float(row['score'])) < 0.11
    assert {row['id'] for row in document['not_ranked']} == {'codex-desktop', 'cursor-desktop'}
