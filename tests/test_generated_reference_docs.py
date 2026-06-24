from __future__ import annotations

from scripts.generate_reference_docs import generated_docs


def test_generated_reference_docs_are_current():
    for path, expected in generated_docs().items():
        assert path.exists(), f"missing generated doc: {path}"
        assert path.read_text(encoding="utf-8") == expected


def test_generated_reference_docs_cover_key_surfaces():
    docs = {path.name: content for path, content in generated_docs().items()}

    assert "aegis verify" in docs["cli-reference.md"]
    assert "aegis deksktop" not in docs["cli-reference.md"]
    assert "/trace" in docs["slash-commands.md"]
    assert "/api/tools/inventory" in docs["api-routes.md"]
    assert "| bash |" in docs["tools-reference.md"]
