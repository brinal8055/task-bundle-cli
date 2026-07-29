from pathlib import Path

from task_bundle.bundle.canonical import sha256_digest
from task_bundle.bundle.loader import load_bundle

ROOT = Path(__file__).resolve().parents[1]


def test_openlibrary_bundle_preserves_immutable_record_fields() -> None:
    bundle = load_bundle(ROOT / "bundles/swebench-pro-openlibrary")
    provenance = bundle.task.provenance

    assert bundle.task.repository.commit == "b70f9abab445676042e5c300dcf5dd8eac4afd18"
    assert provenance is not None
    assert provenance.dataset_revision == "7ab5114912baf22bb098818e604c02fe7ad2c11f"
    assert (
        provenance.instance_id
        == "instance_internetarchive__openlibrary-"
        "e010b2a13697de70170033902ba2e27a1e1acbe9-"
        "v0f5aece3601a5b4419f7ccec1dbda2071be28ee4"
    )
    assert provenance.source_record_sha256 == (
        "sha256:c48f1cee513d00ebe7093c3e6c08590d971f1827b72a4e7cce7140acad396d3a"
    )
    assert sha256_digest(
        (bundle.root / bundle.task.evaluation.test_patch).read_bytes()
    ) == "sha256:8058782e9290177a8e2cf71d7472b09ccdc0431268766bcac00072ca83a4ddb9"
    assert sha256_digest(
        (bundle.root / bundle.task.evaluation.golden_patch).read_bytes()
    ) == "sha256:94dfb2ada929e197ddd66059b67b1feacad88103fd99acb7797eb1e3f6c42f3e"


def test_submission_example_is_loadable_without_generated_state() -> None:
    root = ROOT / "submission/example-bundle"
    bundle = load_bundle(root)

    assert bundle.task.repository.commit == "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
    assert not (root / ".task").exists()
    assert not (root / "artifacts").exists()
