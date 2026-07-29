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


def test_selected_real_bundle_preserves_immutable_record_fields() -> None:
    bundle = load_bundle(ROOT / "bundles/swebench-pro-ansible-d9f186")
    provenance = bundle.task.provenance

    assert bundle.task.repository.commit == "59ca05b70994b07a9507f61a0871146a4991b262"
    assert provenance is not None
    assert provenance.dataset_revision == "7ab5114912baf22bb098818e604c02fe7ad2c11f"
    assert (
        provenance.instance_id
        == "instance_ansible__ansible-"
        "d9f1866249756efc264b00ff7497e92c11a9885f-"
        "v0f01c69f1e2528b935359cfe578530722bca2c59"
    )
    assert provenance.source_record_sha256 == (
        "sha256:d9ac34c26a511a63954f1dd21f9cfea6eea56b8a96437fee2d9ab47aded9d994"
    )
    assert sha256_digest(
        (bundle.root / bundle.task.evaluation.test_patch).read_bytes()
    ) == "sha256:ef22b72858cfa7b69f0c860fbf87fe296e7d7b1516d6c30a59e3b328e345a832"
    assert sha256_digest(
        (bundle.root / bundle.task.evaluation.golden_patch).read_bytes()
    ) == "sha256:085236f733a15425970deb71e82f48a39d8c959fd2bd47ea79adc5b1c16a8374"
    assert "ENTRYPOINT []" in (bundle.root / "environment/Dockerfile").read_text()


def test_submission_example_is_loadable_without_generated_state() -> None:
    root = ROOT / "submission/example-bundle"
    bundle = load_bundle(root)

    assert bundle.task.repository.commit == "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
    assert not (root / ".task").exists()
    assert not (root / "artifacts").exists()
