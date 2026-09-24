"""Build the token-budgeted repository context that is sent to the AI.

The rendered context is also saved as repository_context.md so you can see exactly what the model
was shown.
"""

from collections import Counter
from pathlib import PurePosixPath

from pydantic import BaseModel

from tutorial_ai.models.repository import LanguageShare
from tutorial_ai.repository.manifests import DeclaredDependency, find_declared_dependencies
from tutorial_ai.repository.scanner import (
    NON_PROGRAMMING_LANGUAGES,
    FileCategory,
    ScannedFile,
    ScanResult,
)
from tutorial_ai.utils.logging import get_logger
from tutorial_ai.utils.text import estimate_tokens

log = get_logger("repository.context")

ENTRY_POINT_NAMES = frozenset({
    "main.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py", "cli.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts", "app.js", "app.ts",
    "index.jsx", "index.tsx", "main.jsx", "main.tsx", "App.jsx", "App.tsx", "App.vue",
    "main.go", "main.rs", "lib.rs", "Main.java", "Program.cs",
})  # fmt: skip

MAX_TREE_ENTRIES = 1500
MIN_USEFUL_CHARS = 1500  # don't bother including a file if less than this fits
MAX_FILES_TO_IGNORE_LISTED = 300

_CATEGORY_PRIORITY = {
    FileCategory.MANIFEST: 1,
    FileCategory.SOURCE: 3,
    FileCategory.CONFIG: 4,
    FileCategory.TEST: 5,
    FileCategory.DATA: 6,
    FileCategory.DOCS: 6,
    FileCategory.OTHER: 7,
}


class FileBlock(BaseModel):
    path: str
    content: str
    truncated: bool


class RepositoryContext(BaseModel):
    repository_name: str
    tree: str
    languages: list[LanguageShare]
    declared_dependencies: list[DeclaredDependency]
    files: list[FileBlock]
    omitted_for_budget: list[str]
    files_to_ignore: list[str]
    all_paths: list[str]  # every scanned file, including ones whose content was not sent
    total_files: int
    ignored_files: int

    def render(self) -> str:
        parts = [f"# Repository: {self.repository_name}", ""]

        parts += ["## Language statistics (by bytes of source files)"]
        parts += [f"- {l.language}: {l.percent:.1f}% ({l.files} files)" for l in self.languages]
        if not self.languages:
            parts.append("- (no recognised source files)")

        parts += ["", f"## File tree ({self.total_files} files; [skipped: reason] = content not shown)"]
        parts += [self.tree]

        if self.declared_dependencies:
            parts += ["", "## Dependencies declared in manifests"]
            for dep in self.declared_dependencies:
                version = f" {dep.version}" if dep.version else ""
                dev = " (dev)" if dep.dev else ""
                parts.append(f"- {dep.name}{version}{dev} — {dep.manifest}")

        parts += ["", "## File contents", ""]
        for block in self.files:
            marker = " (TRUNCATED)" if block.truncated else ""
            parts += [f"<<<FILE {block.path}{marker}>>>", block.content.rstrip("\n"), "<<<END FILE>>>", ""]

        if self.omitted_for_budget:
            parts += ["## Files whose content was omitted to stay within the context budget"]
            parts += [f"- {p}" for p in self.omitted_for_budget]
        return "\n".join(parts) + "\n"

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.render())

    @property
    def truncated_files(self) -> list[str]:
        return [b.path for b in self.files if b.truncated]


def file_priority(file: ScannedFile) -> tuple[int, int, str]:
    """Lower sorts first. README first, then manifests, entry points, source (shallow first)..."""
    path = PurePosixPath(file.path)
    depth = len(path.parts) - 1
    if depth == 0 and path.name.lower().startswith("readme"):
        rank = 0
    elif path.name in ENTRY_POINT_NAMES and file.category is FileCategory.SOURCE:
        rank = 2
    else:
        rank = _CATEGORY_PRIORITY.get(file.category, 7)
    return rank, depth, file.path


def build_context(
    scan: ScanResult,
    repository_name: str,
    max_context_tokens: int,
    max_chars_per_file: int,
) -> RepositoryContext:
    tree = render_tree(scan.files)
    declared = find_declared_dependencies(scan)
    languages = language_statistics(scan.files)

    budget_chars = max_context_tokens * 4 - len(tree) - 4_000  # headroom for headings/deps list
    blocks: list[FileBlock] = []
    omitted: list[str] = []

    for file in sorted((f for f in scan.files if f.content_allowed), key=file_priority):
        try:
            content = (scan.root / file.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("Could not read %s: %s", file.path, exc)
            continue

        limit = min(max_chars_per_file, budget_chars)
        if len(content) > limit and limit < MIN_USEFUL_CHARS:
            omitted.append(file.path)
            continue
        truncated = len(content) > limit
        if truncated:
            remaining = len(content) - limit
            content = content[:limit] + f"\n... [truncated: {remaining:,} more characters]"
        blocks.append(FileBlock(path=file.path, content=content, truncated=truncated))
        budget_chars -= len(content) + len(file.path) + 40

    ignored = [f for f in scan.files if not f.content_allowed]
    files_to_ignore = scan.ignored_directories + [f"{f.path} ({f.skip_reason})" for f in ignored]
    if len(files_to_ignore) > MAX_FILES_TO_IGNORE_LISTED:
        extra = len(files_to_ignore) - MAX_FILES_TO_IGNORE_LISTED
        files_to_ignore = files_to_ignore[:MAX_FILES_TO_IGNORE_LISTED] + [f"... and {extra} more"]

    context = RepositoryContext(
        repository_name=repository_name,
        tree=tree,
        languages=languages,
        declared_dependencies=declared,
        files=blocks,
        omitted_for_budget=omitted,
        files_to_ignore=files_to_ignore,
        all_paths=[f.path for f in scan.files],
        total_files=len(scan.files),
        ignored_files=len(ignored),
    )
    log.info(
        "Context: %d files included (%d truncated), %d omitted for budget, ~%d tokens",
        len(blocks), len(context.truncated_files), len(omitted), context.estimated_tokens,
    )
    return context


def language_statistics(files: list[ScannedFile]) -> list[LanguageShare]:
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for f in files:
        if f.language and f.language not in NON_PROGRAMMING_LANGUAGES and f.category in {
            FileCategory.SOURCE,
            FileCategory.TEST,
        }:
            counts[f.language] += 1
            sizes[f.language] += f.size_bytes
    total = sum(sizes.values())
    if total == 0:
        return [
            LanguageShare(language=lang, files=n, bytes=0, percent=0.0)
            for lang, n in counts.most_common()
        ]
    return [
        LanguageShare(
            language=lang, files=counts[lang], bytes=size, percent=round(100 * size / total, 1)
        )
        for lang, size in sizes.most_common()
    ]


def render_tree(files: list[ScannedFile]) -> str:
    """Indented tree, directories first. Skipped files are marked with the reason."""
    root: dict = {}
    for f in files[:MAX_TREE_ENTRIES]:
        node = root
        *dirs, name = PurePosixPath(f.path).parts
        for d in dirs:
            node = node.setdefault(d + "/", {})
        node[name] = f

    lines: list[str] = []

    def walk(node: dict, indent: int) -> None:
        dirs = sorted(k for k, v in node.items() if isinstance(v, dict))
        leaves = sorted(k for k, v in node.items() if not isinstance(v, dict))
        for d in dirs:
            lines.append("  " * indent + d)
            walk(node[d], indent + 1)
        for name in leaves:
            file: ScannedFile = node[name]
            note = f"  [skipped: {file.skip_reason}]" if file.skip_reason else ""
            lines.append("  " * indent + name + note)

    walk(root, 0)
    if len(files) > MAX_TREE_ENTRIES:
        lines.append(f"... ({len(files) - MAX_TREE_ENTRIES} more files not shown)")
    return "\n".join(lines)


def paths_from_tree(tree: str) -> set[str]:
    """Inverse of render_tree: recover the file paths from a rendered tree."""
    paths: set[str] = set()
    stack: list[str] = []
    for line in tree.splitlines():
        if not line.strip() or line.startswith("... ("):
            continue
        depth = (len(line) - len(line.lstrip(" "))) // 2
        name = line.strip().split("  [skipped:", 1)[0]
        del stack[depth:]
        if name.endswith("/"):
            stack.append(name.rstrip("/"))
        else:
            paths.add("/".join([*stack, name]))
    return paths
