"""Allowlisted release staging; npm is an explicit pre-build step, never a hook."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEMO_FILES = (
    "StreamWorkload.java",
    "StreamProcessor.java",
    "run.sh",
    "campaign.json",
    "avoid-copy.patch",
    "batch-128.patch",
    "demo.py",
    "README.md",
)
WEB_EXTENSIONS = {
    ".html",
    ".js",
    ".css",
    ".woff",
    ".woff2",
    ".svg",
    ".txt",
    ".png",
    ".ico",
}


def _copy_file(source: Path, target: Path):
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Required release input is missing or is a symlink: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _copy_web(source: Path, target: Path):
    if source.is_symlink() or not (source / "index.html").is_file():
        raise ValueError(
            "Production UI is missing. Run python3 scripts/build_distribution.py "
            "or npm --prefix web ci && npm --prefix web run build before uv build."
        )
    total = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts):
            raise ValueError(f"Unexpected release UI input: {relative}")
        if path.is_dir():
            continue
        if path.suffix not in WEB_EXTENSIONS:
            raise ValueError(f"Unexpected release UI file type: {relative}")
        size = path.stat().st_size
        total += size
        if size > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
            raise ValueError("Production UI exceeds release size bounds")
        _copy_file(path, target / relative)
    if not any(target.glob("assets/*.js")):
        raise ValueError("Production UI has no compiled JavaScript assets")


@contextmanager
def _stage(root: Path = ROOT, *, assets: bool = True):
    """Never copy the checkout wholesale or mutate its generated package files."""
    with tempfile.TemporaryDirectory(prefix="autoresearch-release-") as temporary:
        staging = Path(temporary)
        _copy_file(root / "pyproject.toml", staging / "pyproject.toml")
        _copy_file(root / "LICENSE", staging / "LICENSE")
        _copy_file(root / "packaging/backend.py", staging / "packaging/backend.py")
        _copy_file(root / "packaging/README.md", staging / "README.md")
        # Retain this input too so an sdist can stage itself again.
        _copy_file(root / "packaging/README.md", staging / "packaging/README.md")
        source = root / "product/autoresearch"
        for path in sorted(source.glob("*.py")):
            _copy_file(path, staging / "product/autoresearch" / path.name)
        if assets:
            bundled = source / "_web"
            _copy_web(
                bundled if bundled.is_dir() else root / "web/dist",
                staging / "product/autoresearch/_web",
            )
            demo = source / "_demo"
            if not demo.is_dir():
                demo = root / "examples/jvm-streaming"
            for name in DEMO_FILES:
                _copy_file(demo / name, staging / "product/autoresearch/_demo" / name)
        manifest = (
            "include pyproject.toml LICENSE README.md packaging/backend.py packaging/README.md\n"
            "recursive-include product/autoresearch *.py\n"
        )
        if assets:
            manifest += (
                "recursive-include product/autoresearch/_web *\n"
                "recursive-include product/autoresearch/_demo *\n"
            )
        (staging / "MANIFEST.in").write_text(manifest)
        yield staging


def _hook(name, *args, assets=False, **kwargs):
    from setuptools import build_meta

    with _stage(assets=assets) as staging:
        previous = Path.cwd()
        try:
            os.chdir(staging)
            return getattr(build_meta, name)(*args, **kwargs)
        finally:
            os.chdir(previous)


def get_requires_for_build_wheel(config_settings=None):
    return _hook("get_requires_for_build_wheel", config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _hook("get_requires_for_build_sdist", config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _hook(
        "prepare_metadata_for_build_wheel",
        str(Path(metadata_directory).resolve()),
        config_settings,
    )


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _hook(
        "build_wheel",
        str(Path(wheel_directory).resolve()),
        config_settings,
        str(Path(metadata_directory).resolve()) if metadata_directory else None,
        assets=True,
    )


def build_sdist(sdist_directory, config_settings=None):
    return _hook(
        "build_sdist",
        str(Path(sdist_directory).resolve()),
        config_settings,
        assets=True,
    )


# Editable installs intentionally retain source-layout routes and need no UI build.
def get_requires_for_build_editable(config_settings=None):
    from setuptools import build_meta

    return build_meta.get_requires_for_build_editable(config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    from setuptools import build_meta

    return build_meta.prepare_metadata_for_build_editable(
        metadata_directory, config_settings
    )


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    from setuptools import build_meta

    return build_meta.build_editable(
        wheel_directory, config_settings, metadata_directory
    )
