"""
FFmpeg discovery and verification utility.
Handles Linux VPS environments (including non-root, containers, KVM, Nix),
macOS, and Windows without hardcoded paths.
"""

import os
import glob
import shutil
import logging
import subprocess
from pathlib import Path
from typing import Optional

from core.config import FFMPEG_PATH_OVERRIDE

logger = logging.getLogger("musicbot.ffmpeg")

_CACHED_FFMPEG_PATH: Optional[str] = None


def find_ffmpeg() -> Optional[str]:
    """
    Finds the FFmpeg executable dynamically.
    Checks:
    1. FFMPEG_PATH environment variable or .env override.
    2. System PATH (via shutil.which).
    3. Common Linux VPS locations (/usr/bin, /usr/local/bin, /opt/ffmpeg/bin, /snap/bin, ~/.local/bin).
    4. Project directory and subdirectories (./bin/ffmpeg, ./ffmpeg/bin).
    5. Windows WinGet, Chocolatey, and AppData directories.
    6. Bundled imageio-ffmpeg as fallback.
    """
    global _CACHED_FFMPEG_PATH
    if _CACHED_FFMPEG_PATH and os.path.isfile(_CACHED_FFMPEG_PATH):
        return _CACHED_FFMPEG_PATH

    # 1. Configured override
    if FFMPEG_PATH_OVERRIDE:
        candidate = Path(FFMPEG_PATH_OVERRIDE).expanduser()
        if candidate.is_dir():
            candidate = candidate / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if candidate.is_file() and os.access(str(candidate), os.X_OK if os.name != "nt" else os.F_OK):
            _CACHED_FFMPEG_PATH = str(candidate)
            return _CACHED_FFMPEG_PATH
        logger.warning("Configured FFMPEG_PATH is invalid or not executable: %s", FFMPEG_PATH_OVERRIDE)

    # 2. System PATH
    found = shutil.which("ffmpeg")
    if found:
        _CACHED_FFMPEG_PATH = found
        return found

    binary_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    base_dir = Path(__file__).resolve().parent.parent

    # 3. Project root and local directories
    local_candidates = [
        base_dir / binary_name,
        base_dir / "bin" / binary_name,
        base_dir / "ffmpeg" / "bin" / binary_name,
    ]
    for candidate in local_candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK if os.name != "nt" else os.F_OK):
            _CACHED_FFMPEG_PATH = str(candidate)
            return _CACHED_FFMPEG_PATH

    # 4. Standard Linux / Unix VPS paths
    if os.name != "nt":
        linux_candidates = [
            Path("/usr/bin/ffmpeg"),
            Path("/usr/local/bin/ffmpeg"),
            Path("/opt/ffmpeg/bin/ffmpeg"),
            Path("/snap/bin/ffmpeg"),
            Path.home() / ".local" / "bin" / "ffmpeg",
            Path.home() / "bin" / "ffmpeg",
        ]
        for candidate in linux_candidates:
            if candidate.is_file() and os.access(str(candidate), os.X_OK):
                _CACHED_FFMPEG_PATH = str(candidate)
                return _CACHED_FFMPEG_PATH

    # 5. Windows specific paths
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            patterns = [
                os.path.join(local_app, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "ffmpeg.exe"),
                os.path.join(local_app, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
                os.path.join(local_app, "Programs", "ffmpeg", "**", "ffmpeg.exe"),
            ]
            for pattern in patterns:
                matches = sorted(glob.glob(pattern, recursive=True))
                if matches and os.path.isfile(matches[0]):
                    _CACHED_FFMPEG_PATH = matches[0]
                    return _CACHED_FFMPEG_PATH

        windows_standard = [
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
        ]
        for candidate in windows_standard:
            if candidate.is_file():
                _CACHED_FFMPEG_PATH = str(candidate)
                return _CACHED_FFMPEG_PATH

    # 6. Fallback: imageio_ffmpeg
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file():
            _CACHED_FFMPEG_PATH = bundled
            return bundled
    except Exception as exc:
        logger.debug("imageio-ffmpeg fallback check failed: %s", exc)

    return None


def verify_ffmpeg() -> bool:
    """Run a test lavfi sine tone extraction to verify FFmpeg operates properly."""
    ffmpeg_exe = find_ffmpeg()
    if not ffmpeg_exe:
        return False
    try:
        proc = subprocess.run(
            [
                ffmpeg_exe,
                "-nostdin",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "lavfi",
                "-i", "sine=frequency=440:duration=0.1",
                "-f", "s16le",
                "-ar", "48000",
                "-ac", "2",
                "pipe:1"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
        return proc.returncode == 0 and len(proc.stdout) > 0
    except Exception as e:
        logger.error("FFmpeg verification error: %s", e)
        return False
