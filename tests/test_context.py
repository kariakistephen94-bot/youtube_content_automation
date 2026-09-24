from pathlib import Path

from conftest import write

from tutorial_ai.repository.context import build_context, file_priority
from tutorial_ai.repository.scanner import scan_repository


def _context(root: Path, max_tokens: int = 50_000, max_chars: int = 40_000):
    scan = scan_repository(root, 200_000)
    return build_context(scan, "sample", max_context_tokens=max_tokens, max_chars_per_file=max_chars)


def test_secrets_and_ignored_content_never_reach_the_prompt(sample_repo: Path):
    rendered = _context(sample_repo).render()
    assert "super-secret-value" not in rendered
    assert "module.exports" not in rendered  # node_modules
    assert "version = 1" not in rendered  # lockfile contents
    assert "<<<FILE app/main.py>>>" in rendered
    assert "uv.lock  [skipped: lockfile]" in rendered  # still visible in the tree


def test_priority_order(sample_repo: Path):
    context = _context(sample_repo)
    order = [b.path for b in context.files]
    assert order[0] == "README.md"
    assert order.index("pyproject.toml") < order.index("app/main.py")
    assert order.index("app/main.py") < order.index("app/routes/items.py")
    assert order.index("app/routes/items.py") < order.index("tests/test_main.py")


def test_entry_points_rank_above_other_source(tmp_path: Path):
    write(tmp_path, "a/helpers.py", "x=1\n")
    write(tmp_path, "z/main.py", "x=1\n")
    scan = scan_repository(tmp_path, 200_000)
    ranked = sorted(scan.files, key=file_priority)
    assert ranked[0].path == "z/main.py"


def test_per_file_truncation(tmp_path: Path):
    write(tmp_path, "main.py", "x = 1\n" * 2_000)  # 12,000 chars
    context = _context(tmp_path, max_chars=5_000)
    block = context.files[0]
    assert block.truncated
    assert "[truncated:" in block.content
    assert context.truncated_files == ["main.py"]


def test_budget_omits_files_that_do_not_fit(tmp_path: Path):
    for i in range(10):
        write(tmp_path, f"module_{i}.py", f"# module {i}\n" + "value = 1\n" * 1_500)  # ~15k chars each
    context = _context(tmp_path, max_tokens=10_000)  # ~40k chars budget
    assert context.omitted_for_budget, "expected some files to be omitted"
    assert context.estimated_tokens <= 10_000
    assert set(context.all_paths) == {f"module_{i}.py" for i in range(10)}


def test_language_statistics_ignore_docs_and_config(sample_repo: Path):
    context = _context(sample_repo)
    languages = {l.language for l in context.languages}
    assert "Python" in languages
    assert "Markdown" not in languages and "TOML" not in languages


def test_declared_dependencies_listed(sample_repo: Path):
    rendered = _context(sample_repo).render()
    assert "- fastapi >=0.110 — pyproject.toml" in rendered
