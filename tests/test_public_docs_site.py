from __future__ import annotations

from pathlib import Path


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
