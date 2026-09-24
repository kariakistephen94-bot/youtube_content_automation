"""ScreenRecorder: capture the screen with FFmpeg (AVFoundation) on macOS.

Video only: narration is muxed in afterwards from the clean generated audio, so nothing is
recorded from the microphone. Output is scaled/letterboxed to 1920x1080 at 30 fps.
"""

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from tutorial_ai.utils.logging import get_logger

log = get_logger("recording.recorder")

SCREEN_RECORDING_HELP = (
    "macOS blocked screen capture. Open System Settings > Privacy & Security > Screen & System "
    "Audio Recording, turn ON the app you run tutorial-ai from (Claude, Terminal or iTerm), then "
    "quit and reopen that app and try again."
)


class RecorderError(RuntimeError):
    pass


def find_ffmpeg(name: str = "ffmpeg") -> str:
    found = shutil.which(name)
    if not found:
        raise RecorderError(f"{name} is not installed or not on PATH (install FFmpeg).")
    return found


def screen_device_index(ffmpeg: str) -> int:
    """Index of 'Capture screen 0' in FFmpeg's AVFoundation device list."""
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    for line in result.stderr.splitlines():
        match = re.search(r"\[(\d+)\] Capture screen 0", line)
        if match:
            return int(match.group(1))
    raise RecorderError(
        "FFmpeg found no screen capture device. On macOS this means the app running tutorial-ai "
        "has no Screen Recording permission.\n" + SCREEN_RECORDING_HELP
    )


def x11_display() -> str | None:
    """The X display to capture on Linux (the recording sandbox's virtual screen)."""
    import sys

    return os.environ.get("DISPLAY") if sys.platform.startswith("linux") else None


def capture_command(ffmpeg: str, device: int | str | None, output: Path, width: int, height: int, fps: int) -> list[str]:
    """`device=None` captures a synthetic test screen (no permission needed) for pipeline checks;
    an int is a macOS AVFoundation screen index; a string is an X display such as ':99'."""
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p"
    )
    if device is None:
        source = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}"]  # -re: real time
    elif isinstance(device, str):
        source = ["-f", "x11grab", "-framerate", str(fps), "-video_size", f"{width}x{height}",
                  "-draw_mouse", "1", "-i", f"{device}+0,0"]
    else:
        source = ["-f", "avfoundation", "-framerate", str(fps), "-capture_cursor", "1",
                  "-pixel_format", "uyvy422",
                  "-i", f"{device}:none"]
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *source,
        "-vf", scale, "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ]


FRAMES_TIMEOUT = 8.0  # seconds to wait for the first frames before declaring the capture dead


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, command line) for every process on the machine."""
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True)
    table: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def screen_captures(table: dict[int, tuple[int, str]] | None = None) -> tuple[list[int], list[tuple[int, int, str]]]:
    """FFmpeg screen captures split into stale ones (their owner is gone) and live ones
    (pid, owner pid, owner command) that belong to a recording still in progress."""
    table = process_table() if table is None else table
    stale: list[int] = []
    live: list[tuple[int, int, str]] = []
    for pid, (ppid, command) in table.items():
        argv = command.split()
        executable = Path(argv[0]).name if argv else ""
        capture = "avfoundation" in argv or "x11grab" in argv
        if executable != "ffmpeg" or not capture or "-list_devices" in argv:
            continue  # only real captures, not a shell whose command line mentions ffmpeg
        if ppid <= 1 or ppid not in table:
            stale.append(pid)
        else:
            live.append((pid, ppid, table[ppid][1]))
    return stale, live


def kill_stale_captures() -> int:
    """Kill leftover FFmpeg screen captures whose owner has died: a hung one silently blocks
    every new capture. A capture whose owner is still running is somebody's recording in
    progress (e.g. a `tutorial-ai record` started from the terminal while the web UI records):
    killing it would pause *their* scene with a cryptic "stopped capturing" error, so refuse to
    start instead. Only one recording can own the screen."""
    stale, live = screen_captures()
    for pid in stale:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if stale:
        log.warning("Killed %d stale screen-capture process(es): %s", len(stale), stale)
        time.sleep(1.0)
    if live:
        pid, owner, command = live[0]
        raise RecorderError(
            f"The screen is already being recorded (ffmpeg pid {pid}, started by pid {owner}: "
            f"{command[:100]}). Another tutorial-ai recording is in progress; wait for it to finish "
            "or stop it, then record again. Only one recording can run at a time."
        )
    return len(stale)


class RecordingLock:
    """One recording at a time on this Mac: the CLI and the web UI share the keyboard, the
    screen and the tutorial's ports. Holds a pid file; a lock whose owner has died is taken over."""

    def __init__(self, path: Path = Path("~/.tutorial-ai/recording.lock").expanduser()) -> None:
        self.path = path
        self._held = False

    def acquire(self) -> None:
        owner = self.owner()
        if owner is not None and owner != os.getpid():
            table = process_table()
            command = table.get(owner, (0, ""))[1][:100]
            raise RecorderError(
                f"Another tutorial-ai recording is already running (pid {owner}: {command}). Wait for it "
                f"to finish or stop it, then record again. (Lock file: {self.path})"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        self._held = True

    def owner(self) -> int | None:
        """Pid of a *running* process holding the lock, or None."""
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return None
        if pid <= 0:
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
        return pid

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self) -> "RecordingLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def describe_exit(code: int) -> str:
    """'exit code 1', or for a signal death what most likely sent it."""
    if code >= 0:
        return f"exit code {code}"
    try:
        name = signal.Signals(-code).name
    except ValueError:
        name = str(-code)
    text = f"killed by {name}"
    if -code == signal.SIGKILL:
        text += (" - most likely another recording started meanwhile and removed this capture; "
                 "run one recording at a time")
    return text


class ScreenRecorder:
    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30, source: str = "screen") -> None:
        self.width, self.height, self.fps = width, height, fps
        self._ffmpeg = find_ffmpeg()
        if source == "test":
            log.warning("Recording a synthetic test screen, not the real screen (source=test)")
            self._device = None
        elif (display := x11_display()) is not None:
            kill_stale_captures()
            self._device = display  # the sandbox's virtual screen: no permission involved
        else:
            kill_stale_captures()
            self._device = screen_device_index(self._ffmpeg)
        self._process: subprocess.Popen | None = None
        self._output: Path = Path()
        self._log_path: Path = Path()
        self._log_file = None
        self.started_at: float | None = None

    def start(self, output: Path) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)  # never mux a stale file from an earlier run
        self._output = output
        command = capture_command(self._ffmpeg, self._device, output, self.width, self.height, self.fps)
        command[command.index("-loglevel") + 1] = "warning"
        log.info("Recording screen to %s", output)
        # FFmpeg's own messages go to a log file next to the video: the only way to learn why a
        # capture stopped, and it never blocks on a full pipe.
        self._log_path = output.with_suffix(".ffmpeg.log")
        self._log_file = open(self._log_path, "w", encoding="utf-8")
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._log_file, text=True
        )
        # Frames are timestamped from process start, so narration offsets are measured from here.
        self.started_at = time.monotonic()
        time.sleep(1.5)  # fail fast if the permission is missing
        if self._process.poll() is not None:
            error = self._read_log()
            self._process = None
            hint = SCREEN_RECORDING_HELP if "permission" in error.lower() or not error else ""
            raise RecorderError(f"FFmpeg could not start recording: {error or 'no output'}\n{hint}")
        # The output file appears with the first encoded frame: prove the capture is alive now,
        # not after a whole scene has been performed for nothing.
        deadline = time.monotonic() + FRAMES_TIMEOUT
        while time.monotonic() < deadline and not (output.exists() and output.stat().st_size > 0):
            if self._process.poll() is not None:
                break
            time.sleep(0.25)
        if not (output.exists() and output.stat().st_size > 0):
            self._process.kill()
            self._process = None
            raise RecorderError(
                f"FFmpeg is not receiving screen frames (no video written within {FRAMES_TIMEOUT:.0f}s). "
                "Usually a stale capture process or a missing Screen Recording permission.\n"
                + SCREEN_RECORDING_HELP
            )

    def _read_log(self) -> str:
        try:
            self._log_file.close()
        except Exception:
            pass
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [l for l in text.splitlines() if "NSKVONotifying" not in l]
        return "\n".join(lines).strip()

    def stop(self) -> float:
        """Stop recording; returns the recorded length in seconds (by wall clock)."""
        if self._process is None:
            return 0.0
        elapsed = time.monotonic() - (self.started_at or time.monotonic())
        if self._process.poll() is not None:  # capture died while we were recording
            code = self._process.returncode
            error = self._read_log()
            self._process = None
            raise RecorderError(f"FFmpeg stopped capturing on its own ({describe_exit(code)}): {error[-800:] or 'no output'}")
        try:
            if self._process.stdin:
                self._process.stdin.write("q")
                self._process.stdin.flush()
            self._process.wait(timeout=20)
        except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        error = self._read_log()
        if error:
            log.warning("ffmpeg: %s", error[-500:])
        self._process = None
        if not self._output.exists() or self._output.stat().st_size < 1024:
            raise RecorderError(f"FFmpeg produced no video: {error[-600:] or 'no output'}")
        log.info("Stopped recording after %.1fs", elapsed)
        return elapsed
