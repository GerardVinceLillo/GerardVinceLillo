#!/usr/bin/env python3
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

REPO_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CLI_NAME_RE = re.compile(r"^[a-z0-9]+$")
MODULE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

IGNORED_DIRS = {
    ".git",
    ".github",
    ".venv",
    ".dart_tool",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".astro",
    ".parcel-cache",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".vscode",
    "reports",
    "dist",
    "build",
    "coverage",
    "android",
    "ios",
    "linux",
    "macos",
    "windows",
    "web",
    "workspaces",
}

IGNORED_FILES = {
    "README.md",
    "LICENSE",
    "AGENTS.md",
    "CLAUDE.md",
    "Dockerfile",
    "Makefile",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
}

IGNORED_SUFFIXES = (
    ".egg-info",
    ".dist-info",
    ".pyc",
    ".pyo",
    ".pyd",
)

IGNORED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".pdf",
    ".ico",
    ".jar",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp4",
    ".mov",
}

ALLOWED_DUNDER_FILES = {
    "__init__.py",
    "__main__.py",
    "__about__.py",
}

TOKEN_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def is_lower_token(value: str) -> bool:
    return bool(TOKEN_RE.fullmatch(value))


def is_lower_file_name(name: str) -> bool:
    return all(is_lower_token(part) for part in name.split("."))


def working_paths() -> list[Path]:
    out: list[Path] = []
    for root, dirs, files in os.walk(Path.cwd(), topdown=True):
        rel_root = Path(root).relative_to(Path.cwd())
        dirs[:] = [
            d
            for d in dirs
            if d not in IGNORED_DIRS and not d.endswith(IGNORED_SUFFIXES)
        ]
        for name in files:
            rel = rel_root / name if str(rel_root) != "." else Path(name)
            out.append(rel)
    return out


def load_pyproject_scripts(pyproject_path: Path) -> list[str]:
    if not pyproject_path.exists() or tomllib is None:
        return []
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8-sig"))
    project = data.get("project", {})
    scripts = project.get("scripts", {}) or {}
    names = list(scripts.keys())

    entry_points = project.get("entry-points", {}) or {}
    console_scripts = entry_points.get("console_scripts", {}) or {}
    names.extend(console_scripts.keys())
    return names


def discover_local_modules(paths: list[Path]) -> set[str]:
    modules: set[str] = set()
    for p in paths:
        parts = p.parts
        if len(parts) == 1 and p.suffix == ".py":
            modules.add(p.stem)
        if len(parts) >= 2 and parts[0] not in IGNORED_DIRS and parts[-1] == "__init__.py":
            modules.add(parts[0])
    return modules


def main() -> int:
    violations: list[str] = []
    repo_name = Path.cwd().name
    if not REPO_NAME_RE.fullmatch(repo_name):
        violations.append(f"repo name must be kebab-case: {repo_name}")

    paths = working_paths()

    for rel in paths:
        parts = rel.parts
        skip = False
        for part in parts[:-1]:
            if part.startswith(".") or part in IGNORED_DIRS or part.endswith(IGNORED_SUFFIXES):
                skip = True
                break
            if not is_lower_token(part):
                violations.append(f"directory not lowercase snake_case or kebab-case: {rel}")
                skip = True
                break
        if skip:
            continue

        name = parts[-1]
        if name.startswith(".") or name in IGNORED_FILES or name.endswith(IGNORED_SUFFIXES):
            continue
        if name in ALLOWED_DUNDER_FILES:
            continue
        if Path(name).suffix.lower() in IGNORED_EXTENSIONS:
            continue
        if not is_lower_file_name(name):
            violations.append(f"file not lowercase snake_case or kebab-case: {rel}")

    local_modules = discover_local_modules(paths)
    py_files = [p for p in paths if p.suffix == ".py"]
    for rel in py_files:
        text = rel.read_text(encoding="utf-8-sig", errors="ignore")
        try:
            tree = ast.parse(text, filename=str(rel))
        except SyntaxError as exc:
            violations.append(f"python parse error in {rel}: {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in local_modules and not MODULE_RE.fullmatch(root):
                        violations.append(f"import module name must be snake_case/lowercase: {root} ({rel})")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root in local_modules and not MODULE_RE.fullmatch(root):
                    violations.append(f"from-import module name must be snake_case/lowercase: {root} ({rel})")

    for cmd in load_pyproject_scripts(Path("pyproject.toml")):
        if not CLI_NAME_RE.fullmatch(cmd):
            violations.append(f"CLI command must be lowercase alnum only: {cmd}")

    if violations:
        print("Naming standard violations:")
        for item in violations:
            print(f"- {item}")
        return 1

    print("Naming checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
