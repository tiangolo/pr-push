from datetime import date
from pathlib import Path

from scripts.prepare_release import get_release_notes_body, prepare_release


def test_prepare_release_updates_version_notes_and_readme(tmp_path: Path) -> None:
    version_file = tmp_path / "pyproject.toml"
    version_file.write_text('[project]\nversion = "0.0.1"\n')
    release_notes_file = tmp_path / "release-notes.md"
    release_notes_file.write_text(
        "# Release Notes\n\n## Latest Changes\n\n* Add PR Push.\n"
    )
    readme_file = tmp_path / "README.md"
    readme_file.write_text("uses: tiangolo/pr-push@0.0.1\n")

    version = prepare_release(
        "minor",
        version_file,
        release_notes_file,
        readme_file,
        date(2026, 8, 8),
    )

    assert version == "0.1.0"
    assert 'version = "0.1.0"' in version_file.read_text()
    assert readme_file.read_text() == "uses: tiangolo/pr-push@0.1.0\n"
    assert release_notes_file.read_text() == (
        "# Release Notes\n\n"
        "## Latest Changes\n\n"
        "## 0.1.0 (2026-08-08)\n\n"
        "* Add PR Push.\n"
    )


def test_get_release_notes_body() -> None:
    content = (
        "# Release Notes\n\n"
        "## Latest Changes\n\n"
        "## 0.1.0 (2026-08-08)\n\n"
        "### Features\n\n"
        "* Add PR Push.\n\n"
        "## 0.0.1 (2026-08-01)\n\n"
        "* Initial version.\n"
    )

    assert get_release_notes_body(content, "0.1.0", Path("release-notes.md")) == (
        "### Features\n\n* Add PR Push.\n"
    )
