"""RecordingSession: record scenes one at a time into scene_NNN.mp4, then build tutorial.mp4.

Failure behaviour (V1): stop at the failing scene, keep everything recorded so far, report the
reason. Re-running from that scene rebuilds the workspace state of the earlier scenes first.
"""

import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from tutorial_ai.automation import macos
from tutorial_ai.automation.browser import BrowserController
from tutorial_ai.automation.executor import ActionExecutor
from tutorial_ai.automation.orchestrator import ScenePlayer
from tutorial_ai.automation.terminal import TerminalController
from tutorial_ai.automation.typing import HumanTypingEngine, TypingProfile, TypingSpeed
from tutorial_ai.automation.vscode import VSCodeController
from tutorial_ai.models.action import ActionType
from tutorial_ai.models.recording import RecordingResult, RecordingsManifest, RecordingStatus
from tutorial_ai.models.scene import Scene, TutorialScenes
from tutorial_ai.models.script import BeatKind
from tutorial_ai.recording.recorder import RecorderError, RecordingLock, ScreenRecorder

BROWSER_BEATS = (BeatKind.OPEN_BROWSER, BeatKind.TRY_ENDPOINT)
from tutorial_ai.storage import ProjectPaths
from tutorial_ai.tutorial.rehearsal import clean_env, port_in_use, url_port
from tutorial_ai.video.processor import AudioClip, VideoProcessor
from tutorial_ai.utils.logging import get_logger

log = get_logger("recording.session")


class RecordingSession:
    def __init__(
        self,
        paths: ProjectPaths,
        workspace: Path,
        scenes: TutorialScenes,
        manifest: RecordingsManifest,
        *,
        workspace_root: Path,
        speed: TypingSpeed = TypingSpeed.NORMAL,
        audible: bool = False,
        source: str = "screen",
        on_progress: Callable[[str], None] | None = None,
        on_scene_done: Callable[[RecordingsManifest], None] | None = None,
    ) -> None:
        self.paths = paths
        self.workspace = workspace.resolve()
        self._workspace_root = workspace_root.resolve()
        self.scenes = scenes
        self.manifest = manifest
        self._audible = audible
        self._source = source
        self._progress = on_progress or (lambda message: None)
        self._on_scene_done = on_scene_done
        self._vscode = VSCodeController(workspace, HumanTypingEngine(TypingProfile.preset(speed), seed=None))
        self._terminal = TerminalController(self._vscode)
        # Sample files for demo requests live next to the build folder, out of the viewer's sight.
        self._browser = BrowserController(samples_dir=self.workspace.parent.parent / "samples")
        self._executor = ActionExecutor(self._vscode, self._terminal, self._browser)
        self._player = ScenePlayer(self._executor, paths.root, audible, on_progress)
        self._processor = VideoProcessor()
        self._lock = RecordingLock()

    def record(self, scene_ids: list[str] | None = None, from_scene: str | None = None) -> RecordingsManifest:
        macos.require_accessibility()
        self._lock.acquire()  # one recording at a time: the CLI and the web UI share the screen
        try:
            return self._record(scene_ids, from_scene)
        finally:
            self._lock.release()

    def _record(self, scene_ids: list[str] | None, from_scene: str | None) -> RecordingsManifest:
        recorder = ScreenRecorder(source=self._source)
        all_ids = [s.id for s in self.scenes.scenes]
        start_index = all_ids.index(from_scene) if from_scene else 0
        wanted = scene_ids or all_ids[start_index:]
        from tutorial_ai.automation.prerequisites import check_prerequisites

        self._progress("Checking recording prerequisites")
        check_prerequisites([
            action.parameters.command for scene in self.scenes.scenes
            for action in scene.actions if action.type is ActionType.RUN_COMMAND
        ])
        self._check_ports(wanted)

        self._prepare_workspace()
        self._vscode.launch()
        self._vscode.maximize_window()
        try:
            if start_index > 0 or (scene_ids and scene_ids[0] != all_ids[0]):
                first = all_ids.index(wanted[0])
                self._progress(f"Rebuilding the workspace state of scenes 1-{first} (not recorded)")
                self._restore_state(self.scenes.scenes[:first])

            for scene in self.scenes.scenes:
                if scene.id not in wanted:
                    continue
                result = self._record_scene(scene, recorder)
                self.manifest.put(result)
                if self._on_scene_done:
                    self._on_scene_done(self.manifest)
                if result.status is not RecordingStatus.SUCCESS:
                    self._progress(f"Stopped at {scene.id}: {result.error}")
                    break
        finally:
            self._stop_tutorial_processes()
        return self.manifest

    def _stop_tutorial_processes(self) -> None:
        """Leave nothing behind: the tutorial's dev servers, its browser and its VS Code instance."""
        for scene in self.scenes.scenes:
            for beat in scene.beats:
                if beat.kind in BROWSER_BEATS and beat.url and (port := url_port(beat.url)):
                    if port_in_use(port):
                        kill_own_listener(port, self._workspace_root)
        self._browser.close()
        self._vscode.quit_profile_instance()

    def build(self) -> RecordingsManifest:
        videos = []
        for scene in self.scenes.scenes:
            result = self.manifest.get(scene.id)
            if result is None or result.status is not RecordingStatus.SUCCESS or not result.video_path:
                raise RecorderError(f"{scene.id} has no successful recording yet.")
            videos.append(self.paths.root / result.video_path)
        output = self._processor.concatenate(videos, self.paths.final_video)
        from tutorial_ai.audio.duration import media_duration

        self.manifest.final_video_path = str(output.relative_to(self.paths.root))
        self.manifest.final_duration_seconds = round(media_duration(output), 2)
        return self.manifest

    # --- scenes ---------------------------------------------------------------------------

    def _record_scene(self, scene: Scene, recorder: ScreenRecorder) -> RecordingResult:
        raw = self.paths.scenes_dir / f"{scene.id}.raw.mp4"
        final = self.paths.scenes_dir / f"{scene.id}.mp4"
        self._progress(f"Recording {scene.id}: {scene.title}")
        result = RecordingResult(scene_id=scene.id, status=RecordingStatus.RECORDING,
                                 recorded_at=datetime.now(UTC))
        try:
            recorder.start(raw)
            clock_start = recorder.started_at or time.monotonic()
            time.sleep(0.5)
            beats, segments = self._player.play(scene, clock_start)
            duration = recorder.stop()
            clips = [
                AudioClip(path=self.paths.root / seg.audio_path, start_seconds=rec.start_seconds)
                for rec in segments
                for seg in scene.narration_segments
                if seg.id == rec.segment_id and seg.audio_path
            ]
            self._processor.mux(raw, clips, final)
            result.status = RecordingStatus.SUCCESS
            result.raw_video_path = str(raw.relative_to(self.paths.root))
            result.video_path = str(final.relative_to(self.paths.root))
            result.duration_seconds = round(duration, 2)
            result.beats, result.segments = beats, segments
            self._progress(f"{scene.id} done ({duration:.0f}s)")
        except (macos.AutomationError, RecorderError, RuntimeError) as exc:
            try:
                recorder.stop()
            except RecorderError as stop_error:  # the capture itself is what died: say so once
                log.error("%s: %s", scene.id, stop_error)
            result.status = RecordingStatus.PAUSED
            result.error = str(exc)
            log.error("%s paused: %s", scene.id, exc)
        return result

    def _prepare_workspace(self) -> None:
        """A fresh teaching workspace. Only ever deletes inside the configured workspace dir."""
        if not self.workspace.is_relative_to(self._workspace_root) or self.workspace == self._workspace_root:
            raise RecorderError(f"Refusing to use {self.workspace} as a workspace (outside {self._workspace_root})")
        area = self.workspace.parent if self.workspace.parent.name == "tutorial_build" else self.workspace
        if area.exists():
            shutil.rmtree(area)  # see Rehearsal.run: never leave a stale project in the parent
        self.workspace.mkdir(parents=True)
        self._vscode.prepare_workspace()

    def _restore_state(self, earlier: list[Scene]) -> None:
        """Write the files and run the commands of earlier scenes, quickly and off camera."""
        fast = ActionExecutor(
            VSCodeController(self.workspace, HumanTypingEngine(TypingProfile.preset(TypingSpeed.FAST))),
            self._terminal,
        )
        for scene in earlier:
            for beat in scene.beats:
                if beat.kind is BeatKind.WRITE_CODE and beat.file:
                    target = self.workspace / beat.file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(beat.code_after or "", encoding="utf-8")
                elif beat.kind is BeatKind.DELETE_FILE and beat.file:
                    (self.workspace / beat.file).unlink(missing_ok=True)
            for action in scene.actions:
                if action.type in (ActionType.RUN_COMMAND, ActionType.OPEN_TERMINAL,
                                   ActionType.FOCUS_TERMINAL, ActionType.KEYBOARD_SHORTCUT):
                    if action.type is ActionType.KEYBOARD_SHORTCUT and action.parameters.model_dump().get("keys") != ["ctrl", "c"]:
                        continue
                    fast.execute(action)

    def _check_ports(self, wanted: list[str]) -> None:
        for scene in self.scenes.scenes:
            if scene.id not in wanted:
                continue
            for beat in scene.beats:
                if beat.kind in BROWSER_BEATS and beat.url:
                    port = url_port(beat.url)
                    if port and port_in_use(port) and kill_own_listener(port, self._workspace_root):
                        continue  # a leftover server from an earlier tutorial run: cleaned up
                    if port and port_in_use(port):
                        raise RecorderError(
                            f"Port {port} is already in use by another program on this Mac "
                            f"({scene.id} expects the tutorial's server there). Stop it and record again."
                        )


def kill_own_listener(port: int, workspace_root: Path) -> bool:
    """Kill the process listening on `port` if it belongs to a tutorial workspace of ours."""
    import os
    import signal
    import subprocess
    import time

    result = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    for pid in (int(p) for p in result.stdout.split() if p.isdigit()):
        info = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        if str(workspace_root) not in info:
            return False
        log.warning("Killing leftover tutorial server on port %d (pid %d)", port, pid)
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    for _ in range(20):  # the socket can take a moment to close after the kill
        if not port_in_use(port):
            return True
        time.sleep(0.25)
    return False
