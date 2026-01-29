from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path) -> Path:
    original = start
    current = start
    if current.is_file():
        current = current.parent

    for _ in range(10):
        if (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            break
        current = current.parent

    return original


def resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    repo_root = find_repo_root(Path.cwd())
    return repo_root / path
