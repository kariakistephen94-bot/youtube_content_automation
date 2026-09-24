"""Provider-agnostic AI interface.

The rest of the application only talks to `AIProvider`. Each task-specific method takes plain
inputs and returns a validated Pydantic model, so a provider can never hand back unchecked JSON.

Methods are added in the phase that implements and tests them (create_actions, explain_code and
repair_scene arrive with the action/recording phases) rather than as unimplemented stubs. Scenes
are built deterministically from the script, so there is no create_scene AI method.
"""

from abc import ABC, abstractmethod

from tutorial_ai.models.repository import RepositoryAnalysis, RepositoryInsights
from tutorial_ai.models.script import ScriptSectionDraft, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest


class AIProviderError(RuntimeError):
    """The provider call failed (network, auth, quota, blocked response...)."""


class AIResponseValidationError(AIProviderError):
    """The provider kept returning output that does not match the required schema."""


class AIProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def analyze_repository(self, repository_context: str) -> RepositoryInsights:
        """Understand a repository from its rendered, token-budgeted context."""

    @abstractmethod
    def create_tutorial_plan(
        self, analysis: RepositoryAnalysis, request: TutorialRequest
    ) -> TutorialPlan:
        """Turn a repository analysis into a section-by-section teaching plan."""

    @abstractmethod
    def create_script_section(self, request: ScriptSectionRequest) -> ScriptSectionDraft:
        """Write the beats (narration + on-screen actions) for one plan section."""
