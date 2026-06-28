from __future__ import annotations

from pathlib import Path
import ast
import re


ROOT = Path(__file__).resolve().parents[1]


def test_site_next_exposes_public_docs_i18n_and_developer_maturity_sections():
    content = (ROOT / "site-next" / "lib" / "content.ts").read_text(encoding="utf-8")
    page = (ROOT / "site-next" / "app" / "page.tsx").read_text(encoding="utf-8")

    for export_name in (
        "documentationPillars",
        "i18nLocales",
        "developerGuideCards",
        "liveQaHighlights",
    ):
        assert f"export const {export_name}" in content
        assert export_name in page

    for section_id in ("docs", "i18n", "developer-guides", "live-qa"):
        assert f'id="{section_id}"' in page

    for required_topic in (
        "Configuration",
        "Messaging",
        "Cron",
        "Sessions",
        "Browser",
        "TTS",
        "Environment Variables",
        "Docker",
        "Hooks",
        "Profile distributions",
        "Integration/plugin docs",
        "Operations contracts",
        "External live QA",
        "File-family depth",
    ):
        assert required_topic in content

    for guide in (
        "Adding platform adapters",
        "Plugin LLM access",
        "Session storage",
        "Context compression and caching",
        "Provider routing",
        "Dashboard and desktop contracts",
        "Security approvals",
    ):
        assert guide in content


def test_mkdocs_nav_includes_public_docs_i18n_user_guides_and_developer_guides():
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

    for nav_label in (
        "Maturity Matrix",
        "Live QA Matrix",
        "Operations Contracts",
        "I18n Status",
        "User Guide",
        "Developer Guide",
        "Adding Platform Adapters",
        "Plugin LLM Access",
        "Session Storage",
        "Context Compression and Caching",
    ):
        assert nav_label in mkdocs

    for rel in (
        "docs/i18n/index.md",
        "docs/i18n/fr/index.md",
        "docs/i18n/es/index.md",
        "docs/i18n/zh-Hans/index.md",
        "docs/i18n/pa/index.md",
        "docs/developer-guide/adding-platform-adapters.md",
        "docs/developer-guide/plugin-llm-access.md",
        "docs/developer-guide/session-storage.md",
        "docs/developer-guide/context-compression-and-caching.md",
        "docs/developer-guide/provider-routing.md",
        "docs/developer-guide/dashboard-desktop-contracts.md",
        "docs/developer-guide/security-approvals.md",
    ):
        path = ROOT / rel
        assert path.is_file(), rel
        text = path.read_text(encoding="utf-8")
        assert "AEGIS" in text
        assert "TODO" not in text


def test_maturity_report_tracks_public_docs_site_and_i18n_gap_bucket():
    from aegis.maturity import build_maturity_report

    report = build_maturity_report(ROOT)
    bucket_ids = {row["id"] for row in report["remaining_gap_buckets"]}
    assert "public_docs_i18n" in bucket_ids
    assert report["summary"]["public_docs_pages"] >= 40
    assert report["summary"]["i18n_locales"] >= 4
    assert report["summary"]["developer_guides"] >= 7


def _markdown_table_rows(path: Path) -> int:
    rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| ") and not line.startswith("| ---") and " Tool " not in line:
            rows += 1
    return rows


def _test_function_count() -> int:
    total = 0
    for path in (ROOT / "tests").glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        total += sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_") for node in ast.walk(tree))
    return total


def test_public_site_counts_match_generated_repo_facts():
    content = (ROOT / "site-next" / "lib" / "content.ts").read_text(encoding="utf-8")
    assert '{ label: "Registered tools", value: "126"' in content
    assert '{ label: "Bundled skills", value: "71"' in content
    assert '{ label: "Test functions", value: "1,750+"' in content
    assert _markdown_table_rows(ROOT / "docs/tools-reference.md") == 126
    assert _test_function_count() >= 1750
    assert "126 registered tools" in (ROOT / "docs/tools.md").read_text(encoding="utf-8")


def test_public_site_docs_hrefs_are_backed_by_next_route_or_mkdocs_file():
    content = (ROOT / "site-next" / "lib" / "content.ts").read_text(encoding="utf-8")
    page = (ROOT / "site-next" / "app" / "page.tsx").read_text(encoding="utf-8")
    hrefs = set(re.findall(r'href: "([^"]+)"', content)) | set(re.findall(r'href="([^"]+)"', page))
    assert hrefs
    assert (ROOT / "site-next" / "app" / "docs" / "[[...slug]]" / "page.tsx").is_file()
    for href in hrefs:
        if href.startswith("#"):
            assert f'id="{href[1:]}"' in page
            continue
        if href == "/docs":
            assert (ROOT / "docs" / "index.md").is_file()
            continue
        if href.startswith("/docs/"):
            doc_rel = href.removeprefix("/docs/").strip("/")
            candidates = [ROOT / "docs" / f"{doc_rel}.md", ROOT / "docs" / doc_rel / "index.md"]
            assert any(candidate.is_file() for candidate in candidates), href
            continue
        assert href.startswith(("http://", "https://")), href


def test_mkdocs_nav_markdown_paths_exist():
    mkdocs = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for rel in re.findall(r": ([A-Za-z0-9_./-]+\.md)\b", mkdocs):
        assert (ROOT / "docs" / rel).is_file(), rel
