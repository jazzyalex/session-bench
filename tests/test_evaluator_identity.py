from pathlib import Path
import shutil

from session_bench.evaluate import (
    EVALUATOR_IMPLEMENTATION_PATHS,
    implementation_digest,
    implementation_inventory,
)


REPO = Path(__file__).parents[1]


def test_evaluator_identity_uses_an_explicit_complete_dependency_set():
    inventory = implementation_inventory()
    assert tuple(inventory) == EVALUATOR_IMPLEMENTATION_PATHS
    assert all((REPO / relative).is_file() for relative in inventory)
    assert implementation_digest() == "fe02e68fbdfa42e6f28ffdb6cc53b47a8ea784367152ba10d552308746364cb3"


def test_non_evaluator_products_cannot_change_evaluation_identity():
    excluded = {
        "session_bench/__main__.py",
        "session_bench/atlas.py",
        "schemas/v1/atlas.schema.json",
        "schemas/v1/registry.schema.json",
        "schemas/v1/surface.schema.json",
    }
    assert excluded.isdisjoint(implementation_inventory())


def test_atlas_and_campaign_files_do_not_change_evaluator_digest(tmp_path):
    shutil.copytree(REPO / "session_bench", tmp_path / "session_bench")
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    baseline = implementation_digest(tmp_path)
    with (tmp_path / "session_bench" / "atlas.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# unrelated atlas change\n")
    (tmp_path / "session_bench" / "campaign_plan.py").write_text("# new campaign module\n", encoding="utf-8")
    campaign_schema = tmp_path / "schemas" / "campaign" / "v1" / "campaign_plan.schema.json"
    campaign_schema.parent.mkdir(parents=True, exist_ok=True)
    campaign_schema.write_text('{"type":"object","description":"unrelated campaign change"}\n', encoding="utf-8")
    campaign_registry = tmp_path / "registries" / "campaign" / "v1" / "implemented.json"
    campaign_registry.parent.mkdir(parents=True, exist_ok=True)
    campaign_registry.write_text('{"description":"unrelated campaign registry change"}\n', encoding="utf-8")
    (tmp_path / "session_bench" / "c03_contract.py").write_text("# new C03 contract module\n", encoding="utf-8")
    (tmp_path / "session_bench" / "c04_fixture.py").write_text("# new C04 fixture module\n", encoding="utf-8")
    c03_schema = tmp_path / "schemas" / "c03" / "v1" / "contract.schema.json"
    c03_schema.parent.mkdir(parents=True, exist_ok=True)
    c03_schema.write_text('{"description":"unrelated C03 schema change"}\n', encoding="utf-8")
    assert implementation_digest(tmp_path) == baseline
    with (tmp_path / "session_bench" / "decoders.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# semantic decoder change\n")
    assert implementation_digest(tmp_path) != baseline
