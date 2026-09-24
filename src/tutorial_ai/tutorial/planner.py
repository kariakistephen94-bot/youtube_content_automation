"""TutorialPlanner: RepositoryAnalysis + user request -> TutorialPlan."""

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.providers.ai.base import AIProvider
from tutorial_ai.repository.context import paths_from_tree
from tutorial_ai.utils.logging import get_logger

log = get_logger("tutorial.planner")

# Warn when the planned length is this far (as a fraction) from the requested length.
DURATION_TOLERANCE = 0.3


class TutorialPlanner:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    def create_plan(
        self, analysis: RepositoryAnalysis, request: TutorialRequest
    ) -> tuple[TutorialPlan, list[str]]:
        log.info(
            "Planning a %s tutorial (~%.0f min) for %s",
            request.audience.value, request.target_duration_minutes, analysis.repository_name,
        )
        plan = self._provider.create_tutorial_plan(analysis, request)
        return finalize_plan(plan, analysis, request)


def finalize_plan(
    plan: TutorialPlan, analysis: RepositoryAnalysis, request: TutorialRequest
) -> tuple[TutorialPlan, list[str]]:
    """Apply the user's explicit choices and check the plan. Returns (plan, warnings)."""
    warnings: list[str] = []
    plan = plan.model_copy(deep=True)
    if request.title:
        plan.title = request.title
    plan.audience = request.audience

    total = sum(s.estimated_duration_seconds for s in plan.sections)
    plan.estimated_duration_seconds = total
    target = request.target_duration_minutes * 60
    if abs(total - target) > DURATION_TOLERANCE * target:
        warnings.append(
            f"planned duration {total / 60:.1f} min differs from the requested "
            f"{request.target_duration_minutes:.0f} min"
        )

    repo_files = paths_from_tree(analysis.project_structure)
    for section in plan.sections:
        for path in section.files_involved:
            if path not in repo_files:
                warnings.append(
                    f"section {section.section_number}: {path!r} is not in the repository "
                    "(fine if it is a teaching-only file)"
                )

    for warning in warnings:
        log.warning("Plan check: %s", warning)
    return plan, warnings
