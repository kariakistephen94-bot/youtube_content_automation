from pathlib import Path

from conftest import EXAMPLE_REPO, FakeProvider, make_insights

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.repository.analyzer import RepositoryAnalyzer
from tutorial_ai.repository.clone import resolve_source
from tutorial_ai.storage import load_model, save_model


def _analyze(settings, insights, repo: Path = EXAMPLE_REPO) -> tuple[RepositoryAnalysis, FakeProvider]:
    provider = FakeProvider(insights)
    source = resolve_source(str(repo), settings.workspace_directory)
    return RepositoryAnalyzer(settings, provider).analyze(source), provider


def test_analysis_combines_ai_insights_with_scanned_facts(settings):
    analysis, provider = _analyze(settings, make_insights())
    assert analysis.repository_name == "fastapi-file-upload"
    assert analysis.ai_model == "fake:fake-model"
    assert analysis.source.kind == "local"
    assert analysis.languages[0].language == "Python"
    assert "app/" in analysis.project_structure and "  main.py" in analysis.project_structure
    assert analysis.scan_summary.files_with_content_sent > 0
    # The provider was shown the actual code.
    assert "<<<FILE app/services/files.py>>>" in provider.received_context
    assert "ALLOWED_CONTENT_TYPES" in provider.received_context


def test_hallucinated_paths_are_removed_and_reported(settings):
    insights = make_insights(
        entry_points=[
            {"path": "./app/main.py", "description": "real"},
            {"path": "app/server.py", "description": "invented"},
        ],
        important_files=[
            {"path": "app/services/files.py", "role": "r", "teaching_notes": "t"},
            {"path": "app/utils/magic.py", "role": "r", "teaching_notes": "t"},
        ],
    )
    analysis, _ = _analyze(settings, insights)
    assert [e.path for e in analysis.entry_points] == ["app/main.py"]
    assert [f.path for f in analysis.important_files] == ["app/services/files.py"]
    assert any("app/server.py" in w for w in analysis.validation_warnings)
    assert any("app/utils/magic.py" in w for w in analysis.validation_warnings)


def test_dependencies_are_reconciled_with_manifests(settings):
    insights = make_insights(
        dependencies=[
            {"name": "fastapi", "purpose": "Web framework"},
            {"name": "sqlalchemy", "purpose": "invented"},
        ]
    )
    analysis, _ = _analyze(settings, insights)
    names = {d.name for d in analysis.dependencies}
    assert "pytest" in names  # declared in pyproject dev group, added back
    assert any("sqlalchemy" in w for w in analysis.validation_warnings)


def test_new_files_in_teaching_order_only_warn(settings):
    insights = make_insights(
        potential_teaching_order=[
            {"title": "Start", "goal": "g", "files": ["main.py"]},  # simplified first version
        ]
    )
    analysis, _ = _analyze(settings, insights)
    assert analysis.potential_teaching_order[0].files == ["main.py"]
    assert any("potential_teaching_order" in w for w in analysis.validation_warnings)


def test_analysis_round_trips_through_storage(settings, tmp_path):
    analysis, _ = _analyze(settings, make_insights())
    path = tmp_path / "repository_analysis.json"
    save_model(analysis, path)
    assert load_model(path, RepositoryAnalysis) == analysis


def test_secret_files_never_reach_the_provider(settings, sample_repo):
    _, provider = _analyze(settings, make_insights(), repo=sample_repo)
    assert "super-secret-value" not in provider.received_context
