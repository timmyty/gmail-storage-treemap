"""Build a source release using an explicit, private-data-free file list."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.1.0"
FILES = (
    "gmail_storage_treemap.py", "gmail_storage_core.py", "Start-Gmail-Storage-Treemap.cmd", "Setup.html",
    "README.md", "CHANGELOG.md", ".gitignore", ".gitattributes",
    "docs/images/gmail-storage-treemap-demo.png",
    "tests/test_storage.py", "tests/test_quota.py", "tests/gui_smoke.py",
    "tools/build_release.py", ".github/workflows/tests.yml",
)


def main():
    destination = ROOT / "dist" / f"Gmail-Storage-Treemap-{VERSION}.zip"
    destination.parent.mkdir(exist_ok=True)
    files = list(FILES)
    if (ROOT / "LICENSE").is_file():
        files.append("LICENSE")
    for name in files:
        if not (ROOT / name).is_file():
            raise FileNotFoundError(f"Missing release file: {name}")
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, f"Gmail-Storage-Treemap-{VERSION}/{name}")
    print(f"Created {destination.name} with {len(files)} explicitly selected files.")


if __name__ == "__main__":
    main()
