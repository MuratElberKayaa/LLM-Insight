"""Install the project's pinned Node.js into `.tools/` (git-ignored), never globally.

Usage (from the repository root):
    python -I scripts/dev/install_node.py            # download, verify, extract
    python -I scripts/dev/install_node.py --print-bin  # print the bin directory only

The archive and `SHASUMS256.txt` come from https://nodejs.org/dist/v<version>/ over HTTPS,
are downloaded into a fresh empty directory, and the archive's SHA-256 must match the
published checksum before anything is extracted. The global Node installation, PATH and npm
configuration are not touched: prepend the printed bin directory to PATH for the frontend
commands only (frontend/README.md). Stdlib only.
"""

import argparse
import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "22.23.4"  # frontend/.nvmrc and package.json "engines" must agree
ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / ".tools"
BASE_URL = f"https://nodejs.org/dist/v{VERSION}/"


def target() -> tuple[str, str]:
    """(archive name, extracted directory name) for this machine."""
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if arch is None:
        sys.exit(f"unsupported architecture: {machine}")
    if sys.platform == "win32":
        name = f"node-v{VERSION}-win-{arch}"
        return f"{name}.zip", name
    os_name = {"linux": "linux", "darwin": "darwin"}.get(sys.platform)
    if os_name is None:
        sys.exit(f"unsupported platform: {sys.platform}")
    name = f"node-v{VERSION}-{os_name}-{arch}"
    return f"{name}.tar.xz", name


def bin_dir(dirname: str) -> Path:
    home = TOOLS / dirname
    return home if sys.platform == "win32" else home / "bin"


def download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)


def expected_sha256(sums: Path, archive: str) -> str:
    for line in sums.read_text(encoding="ascii").splitlines():
        digest, _, name = line.partition("  ")
        if name.strip() == archive:
            return digest.strip()
    sys.exit(f"{archive} is not listed in SHASUMS256.txt")


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract, refusing any member that would land outside `dest`."""
    dest = dest.resolve()

    def check(name: str) -> None:
        if not (dest / name).resolve().is_relative_to(dest):
            sys.exit(f"refusing archive member outside the target: {name!r}")

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                check(name)
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                check(member.name)
            tf.extractall(dest, filter="data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print-bin", action="store_true", help="print the bin directory")
    args = parser.parse_args()
    archive, dirname = target()
    if args.print_bin:
        print(bin_dir(dirname))
        return 0
    if (TOOLS / dirname).exists():
        print(f"already installed: {TOOLS / dirname}")
        return 0
    TOOLS.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="node-dl-") as tmp:
        tmp_dir = Path(tmp)
        sums = tmp_dir / "SHASUMS256.txt"
        download(BASE_URL + "SHASUMS256.txt", sums)
        want = expected_sha256(sums, archive)
        path = tmp_dir / archive
        download(BASE_URL + archive, path)
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != want:
            sys.exit(f"SHA-256 mismatch for {archive}: expected {want}, got {got}")
        print(f"verified {archive} sha256={got}")
        staging = tmp_dir / "extract"
        staging.mkdir()
        safe_extract(path, staging)
        shutil.move(str(staging / dirname), str(TOOLS / dirname))
    print(f"installed: {bin_dir(dirname)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
