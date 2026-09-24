"""RepositoryAnalyzer: scan -> budgeted context -> AI insights -> validated RepositoryAnalysis."""

from datetime import UTC, datetime
from pathlib import Path

from tutorial_ai.config import Settings
from tutorial_ai.models.repository import (
    Dependency,
    RepositoryAnalysis,
    RepositoryInsights,
    RepositorySource,
    ScanSummary,
)
from tutorial_ai.providers.ai.base import AIProvider
from tutorial_ai.repository.context import RepositoryContext, build_context
from tutorial_ai.repository.manifests import DeclaredDependency, normalize_package_name
from tutorial_ai.repository.scanner import scan_repository
from tutorial_ai.utils.logging import get_logger

log = get_logger("repository.analyzer")


class RepositoryAnalyzer:
    def __init__(self, settings: Settings, provider: AIProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider

    def prepare_context(self, source: RepositorySource) -> RepositoryContext:
        """Scan the repository and build the context. No AI call - safe for --dry-run."""
        scan = scan_repository(Path(source.local_path), self._settings.max_file_bytes)
        return build_context(
            scan,
            repository_name=source.name,
            max_context_tokens=self._settings.max_context_tokens,
            max_chars_per_file=self._settings.max_chars_per_file,
        )

    def analyze(
        self, source: RepositorySource, context: RepositoryContext | None = None
    ) -> RepositoryAnalysis:
        if self._provider is None:
            raise RuntimeError("RepositoryAnalyzer.analyze requires an AI provider")
        context = context or self.prepare_context(source)

        log.info("Analysing %s with %s (%s)", source.name, self._provider.name, self._provider.model)
        insights = self._provider.analyze_repository(context.render())
        insights, warnings = validate_insights(insights, context)
        for warning in warnings:
            log.warning("Analysis check: %s", warning)

        return RepositoryAnalysis(
            **insights.model_dump(),
            repository_name=source.name,
            source=source,
            analyzed_at=datetime.now(UTC),
            ai_model=f"{self._provider.name}:{self._provider.model}",
            languages=context.languages,
            project_structure=context.tree,
            files_to_ignore=context.files_to_ignore,
            scan_summary=scan_summary(context),
            validation_warnings=warnings,
        )


def scan_summary(context: RepositoryContext) -> ScanSummary:
    return ScanSummary(
        total_files=context.total_files,
        ignored_files=context.ignored_files,
        files_with_content_sent=len(context.files),
        files_truncated=len(context.truncated_files),
        files_omitted_for_budget=len(context.omitted_for_budget),
        estimated_context_tokens=context.estimated_tokens,
    )


def _normalize_path(path: str) -> str:
    path = path.strip()
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/")


def validate_insights(
    insights: RepositoryInsights, context: RepositoryContext
) -> tuple[RepositoryInsights, list[str]]:
    """Check AI output against the real repository.

    Paths claimed to exist but missing are removed (and reported). Teaching-order files are only
    reported, because a teaching step may legitimately create a file under a new name.
    """
    existing = set(context.all_paths)
    warnings: list[str] = []
    data = insights.model_copy(deep=True)

    def keep(path: str, field: str) -> bool:
        if _normalize_path(path) in existing:
            return True
        warnings.append(f"{field}: removed {path!r} (not in repository)")
        return False

    data.entry_points = [e for e in data.entry_points if keep(e.path, "entry_points")]
    data.important_files = [f for f in data.important_files if keep(f.path, "important_files")]
    for item in data.entry_points + data.important_files:
        item.path = _normalize_path(item.path)
    for concept in data.important_concepts:
        concept.related_files = [
            _normalize_path(p)
            for p in concept.related_files
            if keep(p, f"important_concepts[{concept.name}].related_files")
        ]

    for step in data.potential_teaching_order:
        missing = [p for p in step.files if _normalize_path(p) not in existing]
        if missing:
            warnings.append(
                f"potential_teaching_order[{step.title}]: files not in repository {missing}"
            )

    data.dependencies, dependency_warnings = _reconcile_dependencies(
        data.dependencies, context.declared_dependencies
    )
    warnings += dependency_warnings

    if not data.important_files:
        warnings.append("important_files is empty")
    if not data.potential_teaching_order:
        warnings.append("potential_teaching_order is empty")
    return data, warnings


def _reconcile_dependencies(
    ai_dependencies: list[Dependency], declared: list[DeclaredDependency]
) -> tuple[list[Dependency], list[str]]:
    if not declared:
        return ai_dependencies, []
    warnings: list[str] = []
    declared_by_name = {normalize_package_name(d.name): d for d in declared}
    ai_names = {normalize_package_name(d.name) for d in ai_dependencies}

    for dep in ai_dependencies:
        if normalize_package_name(dep.name) not in declared_by_name:
            warnings.append(f"dependencies: {dep.name!r} is not declared in any parsed manifest")

    result = list(ai_dependencies)
    for name, dep in declared_by_name.items():
        if name not in ai_names:
            result.append(
                Dependency(
                    name=dep.name,
                    version=dep.version,
                    dev=dep.dev,
                    purpose=f"Declared in {dep.manifest}; purpose not identified by the analysis.",
                )
            )
    return result, warnings
