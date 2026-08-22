from pathlib import Path

import yaml


BANNED_MODEL_IDENTITIES = ("gpt-oss", "stheno")
GENERIC_SURFACES = [
    Path("README.md"),
    Path("config/models/catalog.yaml"),
    Path("config/gateway/profiles.yaml"),
    Path("docs/CATALOG.md"),
    Path("docs/MODEL_SOURCES.md"),
    Path("docs/FEATURES.md"),
    *sorted(Path("examples/catalog").glob("*.yaml")),
]


def test_base_catalog_contains_no_model_identity() -> None:
    catalog = yaml.safe_load(Path("config/models/catalog.yaml").read_text(encoding="utf-8"))
    assert catalog["version"] == 2
    assert catalog["models"] == {}
    assert {"chat", "code", "story", "image", "agent"}.issubset(
        catalog["experiences"]
    )


def test_current_generic_surfaces_do_not_name_preferred_models() -> None:
    for path in GENERIC_SURFACES:
        text = path.read_text(encoding="utf-8").lower()
        for identity in BANNED_MODEL_IDENTITIES:
            assert identity not in text, f"{identity} leaked into generic surface {path}"
