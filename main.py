"""
FrostDeck backend.

Runs as the `deck` user inside Decky Loader. Responsibilities:

* Download the official Battle.net installer.
* Remember which Steam shortcut (app id) owns the Battle.net Proton prefix.
* Inspect that prefix to find out whether Battle.net is installed and which
  Blizzard games have been installed through it.
* Persist which games already have Steam shortcuts.

All of the Steam-side work (creating shortcuts, setting Proton, collections,
launching) happens in the frontend through the Steam client API, because
Steam owns the Proton environment, gamescope window, controller input and
on-screen keyboard.
"""

import asyncio
import base64
import concurrent.futures
import glob
import filecmp
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import urllib.parse
import urllib.request
from typing import Any, Optional

import decky

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "py_modules"))

from addons import (  # noqa: E402
    ADDON_CATALOG, addon_by_id, custom_addon_entry, discover_flavors, group_folders, pick_release, read_addon_toc, tukui_supports,
    package_fits, toc_interfaces, wowi_addon_entry, wowi_compatible,
)
from catalog import GAME_CATALOG, SGDB_BATTLENET_ID, STEAM_APP_IDS, resolve_game  # noqa: E402

INSTALLER_URL = (
    "https://www.battle.net/download/getInstallerForGame"
    "?os=win&gameProgram=BATTLENET_APP&version=Live"
)
INSTALLER_FILENAME = "Battle.net-Setup.exe"
USER_AGENT = "FrostDeck/decky"
CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",   # SteamOS / Arch
    "/etc/ssl/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
)
INSTALL_PATH_WIN = r"C:\Program Files (x86)\Battle.net"
INSTALLER_LAUNCH_OPTIONS = f'--lang=enUS --installpath="{INSTALL_PATH_WIN}"'
COLLECTION_NAME = "Blizzard"
SGDB_API = "https://www.steamgriddb.com/api/v2"
STEAM_CDN = "https://cdn.cloudflare.steamstatic.com/steam/apps"
MAX_ASSET_BYTES = 5_000_000   # above this, fall back to SteamGridDB's thumbnail

# SteamGridDB queries per Steam asset kind. Grids double as capsule (portrait)
# and header (landscape) depending on the requested dimensions.
SGDB_ASSET_QUERIES: dict[str, tuple[str, dict[str, str]]] = {
    "capsule": ("/grids/game/{id}", {"dimensions": "600x900", "types": "static", "mimes": "image/png,image/jpeg", "nsfw": "false", "humor": "false"}),
    "header": ("/grids/game/{id}", {"dimensions": "920x430,460x215", "types": "static", "mimes": "image/png,image/jpeg", "nsfw": "false", "humor": "false"}),
    "hero": ("/heroes/game/{id}", {"types": "static", "mimes": "image/png,image/jpeg", "nsfw": "false", "humor": "false"}),
    "logo": ("/logos/game/{id}", {"types": "static", "mimes": "image/png", "nsfw": "false", "humor": "false"}),
    "icon": ("/icons/game/{id}", {"types": "static", "nsfw": "false", "humor": "false"}),
}
STEAM_CDN_ASSETS: dict[str, tuple[str, str]] = {
    "capsule": ("library_600x900_2x.jpg", "jpg"),
    "header": ("header.jpg", "jpg"),
    "hero": ("library_hero.jpg", "jpg"),
    "logo": ("logo.png", "png"),
}

SETTINGS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

# Bump when the exe / launch options written into Steam shortcuts change, so
# the frontend rewrites existing shortcuts once after an update.
SHORTCUT_SCHEMA = 11

DEFAULT_SETTINGS: dict[str, Any] = {
    "shortcut_schema": 0,         # schema the saved shortcuts were written with
    "bnet_app_id": None,          # Steam shortcut app id that owns the prefix
    "bnet_phase": "none",         # none | installer | launcher
    "compat_tool": None,          # Proton tool name chosen by the user
    "game_app_ids": {},           # launch code -> Steam shortcut app id
    "sgdb_api_key": None,         # personal SteamGridDB API key, never leaves the Deck
    "artwork_applied": {},        # launch code (or "bnet") -> True once art has been applied
    "wow_install_dir": None,      # override for where WoW lives; auto-detected when None
    "wow_addons": {},             # flavour dir -> addon id -> {version, folders}
    "custom_addons": [],          # user-added GitHub repos ("owner/name")
    "wowi_addons": {},            # WoWInterface id -> {name, author} for addons installed from there
}

GITHUB_API = "https://api.github.com"
WOWI_API = "https://api.mmoui.com/v3/game/WOW"
TUKUI_API = "https://api.tukui.org/v1"
ADDON_CACHE_TTL = 6 * 3600


def _ssl_context() -> ssl.SSLContext:
    """
    Build an SSL context that can actually verify certificates.

    Decky's bundled Python does not always know where SteamOS keeps its CA
    bundle, which makes the default context fail with "unable to get local
    issuer certificate". Point it at the system bundle when one exists.
    """
    for path in CA_BUNDLE_CANDIDATES:
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _parse_vdf(text: str) -> dict[str, Any]:
    """
    Minimal parser for Valve's text KeyValues format (config.vdf,
    compatibilitytool.vdf). Quoted keys and values, nested braces, `//` comments.
    Keys are kept as written; callers compare case-insensitively.
    """
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
        elif ch == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif ch == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            tokens.append("".join(buf))
            i = j + 1
        elif ch in "{}":
            tokens.append(ch)
            i += 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n{}\"":
                j += 1
            tokens.append(text[i:j])
            i = j

    pos = 0

    def parse_block() -> dict[str, Any]:
        nonlocal pos
        block: dict[str, Any] = {}
        while pos < len(tokens):
            token = tokens[pos]
            if token == "}":
                pos += 1
                return block
            key = token
            pos += 1
            if pos < len(tokens) and tokens[pos] == "{":
                pos += 1
                block[key] = parse_block()
            elif pos < len(tokens):
                block[key] = tokens[pos]
                pos += 1
        return block

    return parse_block()


def _vdf_get(block: Any, *path: str) -> Any:
    """Case-insensitive nested lookup."""
    current = block
    for key in path:
        if not isinstance(current, dict):
            return None
        match = next((k for k in current if k.lower() == key.lower()), None)
        if match is None:
            return None
        current = current[match]
    return current


def _official_proton_tool_name(folder: str) -> Optional[str]:
    """
    Map an official Proton install folder to Steam's internal tool name:
    "Proton - Experimental" -> proton_experimental, "Proton Hotfix" -> proton_hotfix,
    "Proton 11.0" -> proton_11, "Proton 6.3" -> proton_63, "Proton 5.13" -> proton_513.
    """
    lowered = folder.lower()
    if "experimental" in lowered:
        return "proton_experimental"
    if "hotfix" in lowered:
        return "proton_hotfix"
    match = re.match(r"proton (\d+)\.(\d+)", lowered)
    if match:
        major, minor = match.group(1), match.group(2)
        return f"proton_{major}" if minor == "0" else f"proton_{major}{minor}"
    return None


def _steam_root() -> str:
    """
    Resolve the real Steam install directory for the deck user.

    `~/.steam/root` is a symlink on SteamOS; `~/.local/share/Steam` is the
    canonical location. Prefer whichever actually exists.
    """
    home = decky.DECKY_USER_HOME
    candidates = [
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".steam", "steam"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return os.path.realpath(candidate)
    return candidates[0]


def _compatdata_dir(app_id: int) -> str:
    return os.path.join(_steam_root(), "steamapps", "compatdata", str(app_id))


def _prefix_drive_c(app_id: int) -> str:
    return os.path.join(_compatdata_dir(app_id), "pfx", "drive_c")


def _bnet_dir(app_id: int) -> str:
    return os.path.join(_prefix_drive_c(app_id), "Program Files (x86)", "Battle.net")


def _bnet_launcher_exe(app_id: int) -> str:
    """Bootstrapper that self-updates then starts the client. Used for the Battle.net shortcut."""
    return os.path.join(_bnet_dir(app_id), "Battle.net Launcher.exe")


def _bnet_client_exe(app_id: int) -> str:
    """
    The client itself. Game shortcuts target this with `--exec="launch CODE"`,
    matching Lutris's maintained installers; going through the Launcher adds
    `--from-launcher` and the client then ignores the exec command.
    """
    return os.path.join(_bnet_dir(app_id), "Battle.net.exe")


def _bnet_installed(app_id: int) -> bool:
    return os.path.isfile(_bnet_launcher_exe(app_id)) or os.path.isfile(_bnet_client_exe(app_id))


def _bnet_log_dir(app_id: int) -> str:
    return os.path.join(
        _prefix_drive_c(app_id),
        "users", "steamuser", "AppData", "Local", "Battle.net", "Logs",
    )


def _bnet_config(app_id: int) -> str:
    return os.path.join(
        _prefix_drive_c(app_id),
        "users", "steamuser", "AppData", "Roaming", "Battle.net", "Battle.net.config",
    )


class Plugin:
    settings: dict[str, Any]
    download_progress: float
    download_total: int
    download_error: Optional[str]
    download_thread: Optional[threading.Thread]

    # ------------------------------------------------------------------ settings

    def _load_settings(self) -> None:
        self.settings = dict(DEFAULT_SETTINGS)
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                self.settings.update(stored)
        except FileNotFoundError:
            pass
        except Exception as error:  # corrupt file: start fresh but keep a copy
            decky.logger.warning("Could not read settings, resetting: %s", error)
            try:
                shutil.copy(SETTINGS_FILE, SETTINGS_FILE + ".bak")
            except Exception:
                pass

    def _save_settings(self) -> None:
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
            json.dump(self.settings, handle, indent=2)

    # ------------------------------------------------------------------ http

    use_curl: bool = False

    def _http_get(self, url: str, headers: Optional[dict[str, str]] = None, timeout: int = 30) -> bytes:
        """GET a URL, via urllib when its TLS works and via the system curl otherwise."""
        merged = {"User-Agent": USER_AGENT, **(headers or {})}
        if not self.use_curl:
            try:
                request = urllib.request.Request(url, headers=merged)
                with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
                    return response.read()
            except Exception as error:
                decky.logger.info("urllib GET failed (%s); using curl from now on", error)
                self.use_curl = True
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl not found")
        command = [curl, "-sS", "-L", "-f", "--max-time", str(timeout)]
        for name, value in merged.items():
            command += ["-H", f"{name}: {value}"]
        command.append(url)
        result = subprocess.run(command, capture_output=True, timeout=timeout + 10, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"curl exited {result.returncode}: {result.stderr.decode(errors='replace').strip()}")
        return result.stdout

    # ------------------------------------------------------------------ installer

    def _download_dir(self) -> str:
        """
        Directory the installer is saved to. Decky's runtime dir is preferred,
        but on some loader versions it is created root-owned while the plugin
        runs as `deck`, so fall back to a folder under the user's home.
        """
        candidates = [
            decky.DECKY_PLUGIN_RUNTIME_DIR,
            os.path.join(decky.DECKY_USER_HOME, ".local", "share", "FrostDeck"),
        ]
        for candidate in candidates:
            try:
                os.makedirs(candidate, exist_ok=True)
                probe = os.path.join(candidate, ".write-test")
                with open(probe, "w", encoding="utf-8") as handle:
                    handle.write("ok")
                os.remove(probe)
                return candidate
            except OSError as error:
                decky.logger.warning("Download dir %s not writable: %s", candidate, error)
        return candidates[-1]

    def _installer_path(self) -> str:
        return os.path.join(self._download_dir(), INSTALLER_FILENAME)

    def _launch_helper_path(self) -> Optional[str]:
        """
        Copy `defaults/bnet-launch.sh` next to the installer so game shortcuts
        can reference a stable, user-readable path even if the plugin folder is
        root-owned or later replaced. Returns None if the copy fails.
        """
        source = os.path.join(decky.DECKY_PLUGIN_DIR, "defaults", "bnet-launch.sh")
        # Deliberately outside Decky's plugin/data folders: game shortcuts reference
        # this path, and they must keep working if the plugin is uninstalled.
        helper_dir = os.path.join(decky.DECKY_USER_HOME, ".local", "share", "FrostDeck")
        target = os.path.join(helper_dir, "bnet-launch.sh")
        try:
            os.makedirs(helper_dir, exist_ok=True)
            if not os.path.isfile(target) or not filecmp.cmp(source, target, shallow=False):
                shutil.copyfile(source, target)
            os.chmod(target, 0o755)
            return target
        except OSError as error:
            decky.logger.warning("Could not install launch helper: %s", error)
            return None

    def _helper_log_path(self) -> str:
        return os.path.join(decky.DECKY_USER_HOME, ".local", "share", "FrostDeck", "bnet-launch.log")

    def _installer_ready(self) -> bool:
        path = self._installer_path()
        return os.path.isfile(path) and os.path.getsize(path) > 1_000_000

    def _download_with_urllib(self, temp: str) -> None:
        request = urllib.request.Request(INSTALLER_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as response, open(temp, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            self.download_total = total
            received = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if total:
                    self.download_progress = min(received / total, 0.99)

    def _download_with_curl(self, temp: str) -> None:
        """Fallback that relies on the system curl, which always trusts the OS CA store."""
        curl = shutil.which("curl")
        if not curl:
            raise RuntimeError("curl not found for fallback download")
        try:
            head = subprocess.run(
                [curl, "-sIL", "-A", USER_AGENT, INSTALLER_URL],
                capture_output=True, text=True, timeout=60, check=False,
            ).stdout
            lengths = [line.split(":", 1)[1].strip() for line in head.splitlines() if line.lower().startswith("content-length:")]
            self.download_total = int(lengths[-1]) if lengths else 0
        except Exception:
            self.download_total = 0

        proc = subprocess.Popen(
            [curl, "-L", "-sS", "-f", "-A", USER_AGENT, "-o", temp, INSTALLER_URL],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        while proc.poll() is None:
            if self.download_total and os.path.exists(temp):
                self.download_progress = min(os.path.getsize(temp) / self.download_total, 0.99)
            time.sleep(0.3)
        if proc.returncode != 0:
            stderr = (proc.stderr.read() if proc.stderr else "").strip()
            raise RuntimeError(f"curl exited {proc.returncode}: {stderr}")

    def _download_worker(self) -> None:
        """Blocking download, run on a thread. Writes to a temp file then renames."""
        destination = self._installer_path()
        temp = destination + ".part"
        decky.logger.info("Downloading Battle.net installer to %s", destination)
        try:
            try:
                self._download_with_urllib(temp)
            except Exception as error:  # SSL, DNS, proxy: whatever it is, curl may still work
                decky.logger.warning("urllib download failed (%s); retrying with curl", error)
                self.download_progress = 0.0
                self._download_with_curl(temp)
            if not os.path.isfile(temp) or os.path.getsize(temp) < 1_000_000:
                raise RuntimeError("Downloaded file is missing or too small")
            os.replace(temp, destination)
            self.download_progress = 1.0
            decky.logger.info("Downloaded Battle.net installer to %s", destination)
        except Exception as error:
            decky.logger.exception("Installer download failed")
            self.download_error = f"{type(error).__name__}: {error}"
            try:
                os.remove(temp)
            except OSError:
                pass

    async def download_installer(self) -> dict[str, Any]:
        """
        Start downloading the Battle.net installer if it is not already present.
        Returns immediately; poll `get_download_status` for progress.
        """
        if self._installer_ready():
            self.download_progress = 1.0
            return {"started": False, "ready": True, "path": self._installer_path()}
        if self.download_thread and self.download_thread.is_alive():
            return {"started": False, "ready": False, "path": self._installer_path()}
        self.download_progress = 0.0
        self.download_total = 0
        self.download_error = None
        self.download_thread = threading.Thread(target=self._download_worker, daemon=True)
        self.download_thread.start()
        return {"started": True, "ready": False, "path": self._installer_path()}

    async def get_download_status(self) -> dict[str, Any]:
        running = bool(self.download_thread and self.download_thread.is_alive())
        return {
            "running": running,
            "progress": self.download_progress,
            "total_bytes": self.download_total,
            "error": self.download_error,
            "ready": self._installer_ready(),
            "path": self._installer_path(),
        }

    # ------------------------------------------------------------------ state

    async def get_state(self) -> dict[str, Any]:
        """Everything the frontend needs to render, in one call."""
        app_id = self.settings.get("bnet_app_id")
        bnet_installed = bool(app_id) and _bnet_installed(int(app_id))
        return {
            "steam_root": _steam_root(),
            "shortcut_schema": self.settings.get("shortcut_schema", 0),
            "current_shortcut_schema": SHORTCUT_SCHEMA,
            "installer_ready": self._installer_ready(),
            "installer_path": self._installer_path(),
            "installer_launch_options": INSTALLER_LAUNCH_OPTIONS,
            "bnet_app_id": app_id,
            "bnet_phase": self.settings.get("bnet_phase", "none"),
            "bnet_installed": bnet_installed,
            "bnet_exe": _bnet_launcher_exe(int(app_id)) if app_id else None,
            "bnet_client_exe": _bnet_client_exe(int(app_id)) if app_id else None,
            "bnet_start_dir": _bnet_dir(int(app_id)) if app_id else None,
            "compatdata_dir": _compatdata_dir(int(app_id)) if app_id else None,
            "bnet_log_dir": _bnet_log_dir(int(app_id)) if app_id else None,
            "launch_helper": self._launch_helper_path(),
            "helper_log": self._helper_log_path(),
            "compat_tool": self.settings.get("compat_tool"),
            "collection_name": COLLECTION_NAME,
            "games": self._scan_games() if bnet_installed else [],
            "game_app_ids": self.settings.get("game_app_ids", {}),
            "steam_default_compat_tool": await self.get_app_compat_tool(0),
            "wow": self._wow_state() if bnet_installed else None,
            "sgdb_api_key_set": bool(self.settings.get("sgdb_api_key")),
            "sgdb_plugin_installed": self._sgdb_plugin_installed(),
            "artwork_applied": self.settings.get("artwork_applied", {}),
        }

    async def set_bnet_shortcut(self, app_id: int, phase: str) -> None:
        """Record the Steam shortcut that owns the Battle.net prefix and its phase."""
        self.settings["bnet_app_id"] = int(app_id)
        self.settings["bnet_phase"] = phase
        self._save_settings()

    async def set_shortcut_schema(self, version: int) -> None:
        self.settings["shortcut_schema"] = int(version)
        self._save_settings()

    async def set_compat_tool(self, tool_name: Optional[str]) -> None:
        self.settings["compat_tool"] = tool_name
        self._save_settings()

    async def set_game_app_id(self, launch_code: str, app_id: Optional[int]) -> None:
        mapping: dict[str, int] = dict(self.settings.get("game_app_ids", {}))
        if app_id is None:
            mapping.pop(launch_code, None)
        else:
            mapping[launch_code] = int(app_id)
        self.settings["game_app_ids"] = mapping
        self._save_settings()

    def _sgdb_plugin_installed(self) -> bool:
        """Whether the Decky SteamGridDB plugin (manual artwork picking) is installed alongside this one."""
        plugins_dir = os.path.dirname(decky.DECKY_PLUGIN_DIR)
        try:
            return any("steamgriddb" in name.lower() for name in os.listdir(plugins_dir))
        except OSError:
            return False

    async def set_sgdb_api_key(self, key: Optional[str]) -> None:
        cleaned = (key or "").strip() or None
        self.settings["sgdb_api_key"] = cleaned
        self.settings["artwork_applied"] = {}   # a new key means art may now be available
        self._save_settings()

    async def set_artwork_applied(self, code: str, applied: bool) -> None:
        done: dict[str, bool] = dict(self.settings.get("artwork_applied", {}))
        if applied:
            done[code] = True
        else:
            done.pop(code, None)
        self.settings["artwork_applied"] = done
        self._save_settings()

    async def reset(self, delete_prefix: bool = False) -> None:
        """
        Forget every shortcut mapping. Optionally delete the Proton prefix so the
        next install starts from a clean slate. Shortcut removal itself happens
        in the frontend before this is called.
        """
        app_id = self.settings.get("bnet_app_id")
        if delete_prefix and app_id:
            shutil.rmtree(_compatdata_dir(int(app_id)), ignore_errors=True)
        self.settings = dict(DEFAULT_SETTINGS)
        self._save_settings()

    # ------------------------------------------------------------------ games

    def _scan_games(self) -> list[dict[str, Any]]:
        """
        Read Battle.net's own config to find installed games.

        `Battle.net.config` is JSON. Its `Games` object has one entry per product
        Battle.net knows about; entries that carry a `Resumable` key are the
        ones that have been installed on this machine.
        """
        app_id = self.settings.get("bnet_app_id")
        if not app_id:
            return []
        config_path = _bnet_config(int(app_id))
        if not os.path.isfile(config_path):
            return []
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = json.load(handle)
        except Exception as error:
            decky.logger.warning("Could not parse Battle.net.config: %s", error)
            return []

        games: list[dict[str, Any]] = []
        seen: set[str] = set()
        install_paths = self._product_install_paths()
        for config_key, data in (config.get("Games") or {}).items():
            if config_key == "battle_net" or not isinstance(data, dict):
                continue
            if "Resumable" not in data:
                continue
            # A game whose folder has been deleted (by us or by hand) is not installed,
            # even if Battle.net's config has not caught up yet.
            install_path = install_paths.get(config_key)
            if install_path and not os.path.isdir(install_path):
                continue
            entry = resolve_game(config_key)
            if entry is None:
                decky.logger.info("Unknown Battle.net product key: %s", config_key)
                games.append({
                    "config_key": config_key,
                    "launch_code": config_key.upper(),
                    "name": config_key,
                    "known": False,
                    "steam_app_id": None,
                    "install_path": install_path,
                })
                continue
            if entry["launch_code"] in seen:
                continue
            seen.add(entry["launch_code"])
            games.append({
                "config_key": config_key,
                "launch_code": entry["launch_code"],
                "name": entry["name"],
                "known": True,
                "steam_app_id": STEAM_APP_IDS.get(entry["launch_code"]),
                "install_path": install_path,
            })
        games.sort(key=lambda game: game["name"].lower())
        return games

    def _game_by_code(self, launch_code: str) -> Optional[dict[str, Any]]:
        return next((game for game in self._scan_games() if game["launch_code"] == launch_code), None)

    def _folder_size(self, path: str) -> int:
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    continue
        return total

    async def game_install_info(self, launch_code: str) -> dict[str, Any]:
        """Where a game lives and how much space it takes, for the uninstall confirmation."""
        game = self._game_by_code(launch_code)
        path = (game or {}).get("install_path")
        if not path or not os.path.isdir(path):
            return {"path": path, "size_bytes": 0, "exists": False}
        size = await asyncio.to_thread(self._folder_size, path)
        return {"path": path, "size_bytes": size, "exists": True}

    async def uninstall_game(self, launch_code: str) -> dict[str, Any]:
        """
        Delete a game's install folder, which is what Battle.net's own uninstall
        does; the client shows the game as installable again on its next start.
        Saved settings and screenshots live under the prefix's user folder and
        are left alone. Only folders inside this prefix are ever removed.
        """
        app_id = self.settings.get("bnet_app_id")
        game = self._game_by_code(launch_code)
        path = (game or {}).get("install_path")
        if not app_id or not path:
            raise RuntimeError("Install folder unknown; uninstall it from inside Battle.net")
        root = os.path.realpath(_prefix_drive_c(int(app_id)))
        target = os.path.realpath(path)
        if not target.startswith(root + os.sep) or target in (root, os.path.join(root, "Program Files (x86)"), os.path.join(root, "Program Files")):
            raise RuntimeError(f"Refusing to delete {path}: not a game folder inside the Battle.net prefix")
        if not os.path.isdir(target):
            return {"removed": False, "path": path}
        await asyncio.to_thread(shutil.rmtree, target, True)
        decky.logger.info("Uninstalled %s: removed %s", launch_code, target)
        return {"removed": not os.path.isdir(target), "path": path}

    async def get_catalog(self) -> list[dict[str, str]]:
        """Full list of games this plugin knows how to launch, for reference."""
        return [
            {"launch_code": code, "name": entry["name"]}
            for code, entry in sorted(GAME_CATALOG.items(), key=lambda item: item[1]["name"])
        ]

    # ------------------------------------------------------------------ proton

    async def list_compat_tools(self) -> list[dict[str, str]]:
        """
        Proton builds installed on this Deck, discovered the way Steam does:
        official builds under steamapps/common and community builds under
        compatibilitytools.d (whose compatibilitytool.vdf names them).
        """
        root = _steam_root()
        tools: dict[str, dict[str, str]] = {}
        common = os.path.join(root, "steamapps", "common")
        try:
            for folder in sorted(os.listdir(common)):
                if not folder.lower().startswith("proton") or "easyanticheat" in folder.lower() or "battleye" in folder.lower():
                    continue
                name = _official_proton_tool_name(folder)
                if name and os.path.isfile(os.path.join(common, folder, "proton")):
                    tools[name] = {"name": name, "display_name": folder, "source": "steam"}
        except OSError:
            pass
        for base in (os.path.join(root, "compatibilitytools.d"), "/usr/share/steam/compatibilitytools.d"):
            try:
                entries = sorted(os.listdir(base))
            except OSError:
                continue
            for folder in entries:
                vdf_path = os.path.join(base, folder, "compatibilitytool.vdf")
                if not os.path.isfile(vdf_path):
                    continue
                try:
                    with open(vdf_path, "r", encoding="utf-8", errors="replace") as handle:
                        data = _parse_vdf(handle.read())
                except OSError:
                    continue
                compat = _vdf_get(data, "compatibilitytools", "compat_tools") or {}
                for name, info in compat.items():
                    if not isinstance(info, dict):
                        continue
                    # Only tools that run Windows programs (Proton-likes); the Steam
                    # Linux Runtime entries translate linux->linux and are not offered.
                    from_os = str(_vdf_get(info, "from_oslist") or "").lower()
                    if "windows" not in from_os and "proton" not in name.lower():
                        continue
                    display = str(_vdf_get(info, "display_name") or name)
                    tools[name] = {"name": name, "display_name": display, "source": "custom"}
        return sorted(tools.values(), key=lambda tool: tool["display_name"].lower())

    def _compat_tool_mapping(self) -> dict[str, Any]:
        config_path = os.path.join(_steam_root(), "config", "config.vdf")
        try:
            with open(config_path, "r", encoding="utf-8", errors="replace") as handle:
                data = _parse_vdf(handle.read())
        except OSError as error:
            decky.logger.info("Could not read config.vdf: %s", error)
            return {}
        return _vdf_get(data, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping") or {}

    async def get_app_compat_tool(self, app_id: int) -> Optional[str]:
        """
        The compatibility tool Steam has recorded for an app, from
        config/config.vdf's CompatToolMapping. Entry "0" is Steam's default for
        other titles; a specific app id overrides it. Returns the tool name or
        None when nothing is set for that app.
        """
        entry = self._compat_tool_mapping().get(str(app_id))
        name = _vdf_get(entry, "name") if isinstance(entry, dict) else None
        return str(name) if name else None

    async def frontend_log(self, level: str, message: str) -> None:
        """Let the frontend write into the plugin log, for diagnosing Steam API behaviour on-device."""
        getattr(decky.logger, level if level in ("debug", "info", "warning", "error") else "info")("[frontend] %s", message[:2000])

    # ------------------------------------------------------------------ wow addons

    addon_cache: dict[str, dict[str, Any]] = {}

    def _product_install_paths(self) -> dict[str, str]:
        """
        Install folder per Battle.net product id, from the client's own logs
        ("InstallState (hs_beta): ... installPath=C:/Program Files (x86)/Hearthstone"),
        as Unix paths inside the prefix. Newest mention wins.
        """
        app_id = self.settings.get("bnet_app_id")
        if not app_id:
            return {}
        drive_c = _prefix_drive_c(int(app_id))
        paths: dict[str, str] = {}
        try:
            logs = sorted(glob.glob(os.path.join(_bnet_log_dir(int(app_id)), "battle.net-*.log")), key=os.path.getmtime, reverse=True)[:4]
        except OSError:
            return {}
        for log_path in logs:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for match in re.finditer(r"InstallState \(([^)]+)\):[^\n]*?installPath=([A-Za-z]:/.*?)(?= [A-Za-z]+=|\n|$)", text):
                uid, win_path = match.group(1), match.group(2).rstrip("/")
                if uid not in paths and win_path:
                    paths[uid] = os.path.join(drive_c, *win_path[3:].split("/"))
        return paths

    def _wow_install_dir(self) -> Optional[str]:
        """
        Where World of Warcraft is installed inside the Battle.net prefix: the
        saved override, then the default install path, then any path Battle.net's
        own logs mention for the product.
        """
        app_id = self.settings.get("bnet_app_id")
        if not app_id:
            return None
        drive_c = _prefix_drive_c(int(app_id))
        candidates: list[str] = []
        if self.settings.get("wow_install_dir"):
            candidates.append(str(self.settings["wow_install_dir"]))
        candidates.append(os.path.join(drive_c, "Program Files (x86)", "World of Warcraft"))
        # Battle.net logs every product's install path, e.g.
        #   InstallState (wow_forever): ... installPath=C:/Program Files (x86)/World of Warcraft ...
        # Any product whose uid starts with "wow" is a WoW flavour; its path may
        # or may not be the default folder, so all of them are candidates.
        try:
            logs = sorted(glob.glob(os.path.join(_bnet_log_dir(int(app_id)), "battle.net-*.log")), key=os.path.getmtime, reverse=True)[:3]
            for log_path in logs:
                with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
                for match in re.finditer(r"InstallState \((wow[^)]*)\):[^\n]*?installPath=([A-Za-z]:/.*?)(?= [A-Za-z]+=|\n|$)", text):
                    win_path = match.group(2).rstrip("/")
                    unix_path = os.path.join(drive_c, *win_path[3:].split("/"))
                    # Flavour folders (_retail_, _forever_, ...) sit in the product root; a
                    # logged path may point at the root or at a flavour folder inside it.
                    candidates.append(unix_path)
                    candidates.append(os.path.dirname(unix_path))
        except OSError:
            pass
        for candidate in candidates:
            if os.path.isdir(candidate) and (os.path.isfile(os.path.join(candidate, ".build.info")) or any(
                name.startswith("_") and name.endswith("_") for name in os.listdir(candidate)
            )):
                return candidate
        return None

    def _wow_state(self) -> Optional[dict[str, Any]]:
        install_dir = self._wow_install_dir()
        if not install_dir:
            return None
        flavors = discover_flavors(install_dir)
        installed = self.settings.get("wow_addons", {})
        for flavor in flavors:
            flavor["installed"] = installed.get(flavor["dir"], {})
        return {"install_dir": install_dir, "flavors": flavors}

    async def set_wow_install_dir(self, path: Optional[str]) -> None:
        self.settings["wow_install_dir"] = (path or "").strip() or None
        self._save_settings()

    def _extra_addons(self) -> list[dict[str, Any]]:
        """User-added GitHub repos and WoWInterface addons, which live in settings rather than the shipped catalog."""
        extras = [custom_addon_entry(repo) for repo in self.settings.get("custom_addons", [])]
        for uid, info in self.settings.get("wowi_addons", {}).items():
            extras.append(wowi_addon_entry(uid, info.get("name", f"WoWInterface #{uid}"), info.get("author", "")))
        return extras

    def _all_addons(self) -> list[dict[str, Any]]:
        return list(ADDON_CATALOG) + self._extra_addons()

    def _addon(self, addon_id: str) -> Optional[dict[str, Any]]:
        return addon_by_id(addon_id, self._extra_addons())

    wowi_filelist: Optional[dict[str, Any]] = None   # {"fetched_at", "by_uid": {uid: entry}}

    def _wowi_entries(self, force: bool = False) -> dict[str, dict[str, Any]]:
        """WoWInterface's full addon list (about 8,000 entries), cached for a few hours."""
        cached = self.wowi_filelist
        if cached and not force and time.time() - cached["fetched_at"] < ADDON_CACHE_TTL:
            return cached["by_uid"]
        entries = json.loads(self._http_get(f"{WOWI_API}/filelist.json", timeout=120))
        by_uid = {str(entry["UID"]): entry for entry in entries if entry.get("UID")}
        self.wowi_filelist = {"fetched_at": time.time(), "by_uid": by_uid}
        return by_uid

    def _search_sync(self, query: str, flavor_dir: Optional[str]) -> list[dict[str, Any]]:
        """
        Search the shipped catalog, the user's extras and WoWInterface by name.
        Catalog matches come first; WoWInterface results are ranked by downloads
        and deduplicated against catalog names so the same addon is not offered twice.
        """
        needle = query.strip().lower()
        if len(needle) < 2:
            return []
        wow = self._wow_state()
        flavor = next((f for f in (wow or {}).get("flavors", []) if f["dir"] == flavor_dir), None) if flavor_dir else None
        version = flavor["version"] if flavor else ""
        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for addon in self._all_addons():
            haystack = f"{addon['name']} {addon.get('summary', '')} {addon.get('repo', '')}".lower()
            if needle in haystack:
                seen_names.add(addon["name"].lower())
                results.append({"id": addon["id"], "name": addon["name"], "source": addon["source"], "summary": addon.get("summary", ""),
                                "version": None, "downloads": None, "compatible": None, "known": True})
        try:
            entries = self._wowi_entries()
        except Exception as error:
            decky.logger.info("WoWInterface list unavailable: %s", error)
            entries = {}
        matches = [entry for entry in entries.values() if needle in str(entry.get("UIName", "")).lower()]
        matches.sort(key=lambda entry: int(entry.get("UIDownloadTotal") or 0), reverse=True)
        for entry in matches[:40]:
            name = str(entry.get("UIName", ""))
            if name.lower() in seen_names:
                continue
            results.append({
                "id": f"wowi:{entry['UID']}", "name": name, "source": "wowi", "summary": f"By {entry.get('UIAuthorName', '')} on WoWInterface".replace("By  on", "On"),
                "version": entry.get("UIVersion"), "downloads": int(entry.get("UIDownloadTotal") or 0),
                "compatible": wowi_compatible(entry.get("UICompatibility"), version) if version else None, "known": False,
                "listed_for": [str(item.get("version")) for item in (entry.get("UICompatibility") or [])][:4],
                "updated": int(entry.get("UIDate") or 0) // 1000 or None,
            })
        return results[:50]

    async def search_addons(self, query: str, flavor_dir: Optional[str] = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._search_sync, query, flavor_dir)

    async def add_wowi_addon(self, uid: str) -> dict[str, Any]:
        """Remember a WoWInterface addon so it appears in the list; installing is a separate step."""
        entries = await asyncio.to_thread(self._wowi_entries)
        entry = entries.get(str(uid))
        if not entry:
            raise RuntimeError("That WoWInterface addon was not found")
        known: dict[str, Any] = dict(self.settings.get("wowi_addons", {}))
        known[str(uid)] = {"name": str(entry.get("UIName", "")), "author": str(entry.get("UIAuthorName", ""))}
        self.settings["wowi_addons"] = known
        self._save_settings()
        return {"id": f"wowi:{uid}", "name": known[str(uid)]["name"]}

    def _wow_running(self) -> bool:
        """True while any WoW client process exists in this prefix (Wine exposes Windows command lines in /proc)."""
        for entry in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                with open(entry, "rb") as handle:
                    argv0 = handle.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")
            except OSError:
                continue
            if argv0.replace("/", "\\").lower().endswith("\\wow.exe") or argv0.lower().endswith("wowclassic.exe"):
                return True
        return False

    def _managed_folders(self, flavor_dir: str) -> dict[str, str]:
        """folder -> addon id, for folders this plugin installed or adopted in a flavour."""
        mapping: dict[str, str] = {}
        for addon_id, record in self.settings.get("wow_addons", {}).get(flavor_dir, {}).items():
            for folder in record.get("folders", []):
                mapping[folder] = addon_id
        return mapping

    def _scan_unmanaged(self, flavor: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Addons present in the flavour's AddOns folder that this plugin did not
        install: hand-copied, or from another manager. Grouped into addons, with
        the .toc title/version, and marked adoptable when the main folder matches
        a catalog entry.
        """
        try:
            folders = [name for name in os.listdir(flavor["addons_dir"]) if os.path.isdir(os.path.join(flavor["addons_dir"], name))]
        except OSError:
            return []
        managed = self._managed_folders(flavor["dir"])
        by_folder = {entry.get("folder"): entry["id"] for entry in self._all_addons() if entry.get("folder")}
        results: list[dict[str, Any]] = []
        for key, members in group_folders(folders).items():
            unmanaged_members = [folder for folder in members if folder not in managed]
            if not unmanaged_members:
                continue
            toc = read_addon_toc(os.path.join(flavor["addons_dir"], key))
            results.append({
                "group": key,
                "folders": unmanaged_members,
                "title": toc.get("Title") or key,
                "version": toc.get("Version") or "",
                "adoptable_id": by_folder.get(key),
            })
        results.sort(key=lambda item: item["title"].lower())
        return results

    async def adopt_addon(self, addon_id: str, flavor_dir: str) -> None:
        """
        Take over a hand-installed copy of a catalog addon: record its folders
        and .toc version so it shows as installed and gets update checks. The
        first update then replaces it with a packaged release.
        """
        addon = self._addon(addon_id)
        wow = self._wow_state()
        flavor = next((f for f in (wow or {}).get("flavors", []) if f["dir"] == flavor_dir), None)
        if not addon or not flavor or not addon.get("folder"):
            raise RuntimeError("Nothing to adopt")
        groups = group_folders([
            name for name in os.listdir(flavor["addons_dir"]) if os.path.isdir(os.path.join(flavor["addons_dir"], name))
        ])
        folders = groups.get(addon["folder"])
        if not folders:
            raise RuntimeError(f"{addon['name']} is not present in {flavor['label']}")
        toc = read_addon_toc(os.path.join(flavor["addons_dir"], addon["folder"]))
        state: dict[str, Any] = dict(self.settings.get("wow_addons", {}))
        per_flavor = dict(state.get(flavor_dir, {}))
        per_flavor[addon_id] = {"version": toc.get("Version") or "adopted", "folders": sorted(folders), "installed_at": int(time.time()), "adopted": True}
        state[flavor_dir] = per_flavor
        self.settings["wow_addons"] = state
        self._save_settings()

    async def add_custom_addon(self, repo: str) -> dict[str, Any]:
        """
        Register a GitHub repo ("owner/name") as an installable addon. Verified
        up front: it must publish packaged releases with a release.json manifest.
        """
        cleaned = repo.strip().removeprefix("https://github.com/").strip("/")
        if cleaned.count("/") != 1 or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", cleaned):
            raise RuntimeError("Enter the repo as owner/name, e.g. seblindfors/Immersion")
        entry = custom_addon_entry(cleaned)
        if self._addon(entry["id"]):
            raise RuntimeError("That repo is already in the list")
        try:
            latest = await asyncio.to_thread(self._addon_latest, entry, True)
        except Exception as error:
            text = str(error)
            if "404" in text:
                raise RuntimeError(f"github.com/{cleaned} was not found, or has no releases") from error
            if "no packaged releases" in text:
                raise RuntimeError(f"github.com/{cleaned} publishes no packaged releases (no release.json manifest)") from error
            raise RuntimeError(f"Could not read releases for {cleaned}: {text[:120]}") from error
        custom: list[str] = list(self.settings.get("custom_addons", []))
        custom.append(cleaned)
        self.settings["custom_addons"] = custom
        self._save_settings()
        return {"id": entry["id"], "name": entry["name"], "version": latest["releases"][0]["version"]}

    async def remove_custom_addon(self, addon_id: str) -> None:
        installed_anywhere = any(addon_id in per_flavor for per_flavor in self.settings.get("wow_addons", {}).values())
        if installed_anywhere:
            raise RuntimeError("Remove the addon from every flavour first")
        self.settings["custom_addons"] = [
            repo for repo in self.settings.get("custom_addons", []) if custom_addon_entry(repo)["id"] != addon_id
        ]
        if addon_id.startswith("wowi:"):
            known = dict(self.settings.get("wowi_addons", {}))
            known.pop(addon_id.split(":", 1)[1], None)
            self.settings["wowi_addons"] = known
        self.addon_cache.pop(addon_id, None)
        self._save_settings()

    def _addon_latest(self, addon: dict[str, Any], force: bool = False) -> dict[str, Any]:
        """
        Recent releases of an addon, cached for a few hours (GitHub allows 60
        anonymous API requests an hour; manifest and zip downloads do not count).
          github: {"releases": [{"version", "assets": {name: url}, "manifest": dict|None}]}
                  manifests are fetched lazily by _addon_download_for
          tukui:  {"releases": [{"version", "url", "patches"}]}
        Some projects (WeakAuras) publish Retail and Classic as separate releases,
        so the newest release is not necessarily the right one for a flavour.
        """
        cached = self.addon_cache.get(addon["id"])
        if cached and not force and time.time() - cached["fetched_at"] < ADDON_CACHE_TTL:
            return cached["data"]
        if addon["source"] == "github":
            headers = {"Accept": "application/vnd.github+json"}
            releases_raw = json.loads(self._http_get(f"{GITHUB_API}/repos/{addon['repo']}/releases?per_page=8", headers))
            releases = []
            for release in releases_raw:
                if release.get("draft") or release.get("prerelease"):
                    continue
                assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}
                if "release.json" in assets:
                    releases.append({
                        "version": str(release.get("tag_name") or ""), "assets": assets, "manifest": None,
                        "notes": str(release.get("body") or ""), "published": str(release.get("published_at") or ""),
                        "url": str(release.get("html_url") or ""),
                    })
            if not releases:
                raise RuntimeError(f"{addon['name']} has no packaged releases")
            data: dict[str, Any] = {"releases": releases}
        elif addon["source"] == "tukui":
            info = json.loads(self._http_get(f"{TUKUI_API}/addon/{addon['slug']}"))
            data = {"releases": [{"version": str(info.get("version") or ""), "url": info.get("url"), "patches": [str(p) for p in info.get("patch", [])]}]}
        elif addon["source"] == "wowi":
            details = json.loads(self._http_get(f"{WOWI_API}/filedetails/{addon['uid']}.json"))
            detail = details[0] if isinstance(details, list) and details else {}
            compat = None
            try:
                compat = self._wowi_entries().get(str(addon["uid"]), {}).get("UICompatibility")
            except Exception:
                pass
            data = {"releases": [{"version": str(detail.get("UIVersion") or ""), "url": detail.get("UIDownload"), "compat": compat}]}
        else:
            raise RuntimeError(f"Unknown addon source {addon['source']}")
        self.addon_cache[addon["id"]] = {"fetched_at": time.time(), "data": data}
        return data

    def _addon_download_for(self, addon: dict[str, Any], latest: dict[str, Any], flavor: dict[str, Any]) -> Optional[tuple[str, str]]:
        """(zip url, version) of the newest release that fits a flavour, or None."""
        for release in latest["releases"]:
            if addon["source"] == "github":
                if release["manifest"] is None:
                    try:
                        release["manifest"] = json.loads(self._http_get(release["assets"]["release.json"]))
                    except Exception as error:
                        decky.logger.info("Manifest fetch failed for %s %s: %s", addon["name"], release["version"], error)
                        release["manifest"] = {}
                choice = pick_release(release["manifest"], flavor.get("interface"))
                if choice and choice["filename"] in release["assets"]:
                    return release["assets"][choice["filename"]], release["version"]
            elif addon["source"] == "tukui":
                if release.get("url") and (not flavor.get("version") or tukui_supports(release["patches"], flavor["version"])):
                    return release["url"], release["version"]
            elif addon["source"] == "wowi":
                # WoWInterface's compatibility list lags behind patches; it is shown
                # to the user but the package's own TOCs decide after unpacking.
                if release.get("url"):
                    return release["url"], release["version"]
        return None

    def _addon_row(self, addon: dict[str, Any], flavors: list[dict[str, Any]], refresh: bool) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": addon["id"], "name": addon["name"], "summary": addon.get("summary", ""), "category": addon.get("category", ""),
            "featured": bool(addon.get("featured")), "source": addon["source"], "latest_version": None, "error": None,
            "custom": bool(addon.get("custom")), "repo": addon.get("repo"),
            "flavors": {},
        }
        try:
            latest = self._addon_latest(addon, force=refresh)
            row["latest_version"] = latest["releases"][0]["version"]
        except Exception as error:
            latest = None
            row["error"] = str(error)[:200]
            decky.logger.info("Addon lookup failed for %s: %s", addon["name"], error)
        for flavor in flavors:
            installed = flavor["installed"].get(addon["id"])
            download = self._addon_download_for(addon, latest, flavor) if latest else None
            fits_version = download[1] if download else None
            row["flavors"][flavor["dir"]] = {
                "supported": download is not None,
                "latest_version": fits_version,
                "installed_version": installed.get("version") if installed else None,
                "update_available": bool(installed and fits_version and installed.get("version") != fits_version),
                "fits": installed.get("fits") if installed else None,
            }
        return row

    def _list_addons_sync(self, refresh: bool) -> dict[str, Any]:
        wow = self._wow_state()
        if not wow:
            return {"wow": None, "addons": []}
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda addon: self._addon_row(addon, wow["flavors"], refresh), self._all_addons()))
        unmanaged = {flavor["dir"]: self._scan_unmanaged(flavor) for flavor in wow["flavors"]}
        return {"wow": wow, "addons": rows, "unmanaged": unmanaged, "game_running": self._wow_running()}

    async def list_addons(self, refresh: bool = False) -> dict[str, Any]:
        """Catalog with per-flavour status. `refresh` bypasses the release cache."""
        return await asyncio.to_thread(self._list_addons_sync, refresh)

    def _install_addon_sync(self, addon_id: str, flavor_dir: str) -> dict[str, Any]:
        if self._wow_running():
            raise RuntimeError("World of Warcraft is running; close it before changing addons")
        addon = self._addon(addon_id)
        wow = self._wow_state()
        if not addon or not wow:
            raise RuntimeError("Addon or World of Warcraft install not found")
        flavor = next((f for f in wow["flavors"] if f["dir"] == flavor_dir), None)
        if not flavor:
            raise RuntimeError(f"Flavour {flavor_dir} is not installed")
        latest = self._addon_latest(addon)
        download = self._addon_download_for(addon, latest, flavor)
        if not download:
            raise RuntimeError(f"{addon['name']} has no build for {flavor['label']} {flavor.get('version') or ''}".strip())
        url, version = download
        addons_dir = flavor["addons_dir"]
        os.makedirs(addons_dir, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="frostdeck-addon-") as workdir:
            archive = os.path.join(workdir, "addon.zip")
            with open(archive, "wb") as handle:
                handle.write(self._http_get(url, timeout=300))
            staging = os.path.join(workdir, "unpacked")
            folders: set[str] = set()
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    name = member.filename.replace("\\", "/")
                    if not name or name.startswith("/") or ".." in name.split("/"):
                        continue
                    top = name.split("/", 1)[0]
                    if "/" not in name and not member.is_dir():
                        continue   # stray file at the root of the zip; addons are folders
                    folders.add(top)
                    bundle.extract(member, staging)
            if not folders:
                raise RuntimeError("Archive contained no addon folders")
            # Check the package declares this flavour before touching the install.
            fits = package_fits(staging, sorted(folders), flavor.get("interface"))
            if fits is False:
                declared = sorted({value // 10000 for folder in folders for value in toc_interfaces(os.path.join(staging, folder))})
                majors = ", ".join(f"{m}.x" for m in declared) or "other versions"
                raise RuntimeError(f"{addon['name']} {version} is built for WoW {majors}, not {flavor['label']} {flavor.get('version', '')[:4]}; nothing was changed")
            # Replace folders this addon previously installed, then the new ones.
            previous = set(self.settings.get("wow_addons", {}).get(flavor_dir, {}).get(addon_id, {}).get("folders", []))
            for folder in previous | folders:
                shutil.rmtree(os.path.join(addons_dir, folder), ignore_errors=True)
            for folder in sorted(folders):
                shutil.move(os.path.join(staging, folder), os.path.join(addons_dir, folder))

        state: dict[str, Any] = dict(self.settings.get("wow_addons", {}))
        per_flavor = dict(state.get(flavor_dir, {}))
        per_flavor[addon_id] = {"version": version, "folders": sorted(folders), "installed_at": int(time.time()), "fits": fits}
        state[flavor_dir] = per_flavor
        self.settings["wow_addons"] = state
        self._save_settings()
        decky.logger.info("Installed %s %s into %s (%s), fits=%s", addon["name"], version, flavor_dir, ", ".join(sorted(folders)), fits)
        return {"version": version, "folders": sorted(folders), "fits": fits}

    async def install_addon(self, addon_id: str, flavor_dir: str) -> dict[str, Any]:
        """Download and unpack an addon into one flavour; also used for updates."""
        return await asyncio.to_thread(self._install_addon_sync, addon_id, flavor_dir)

    async def remove_addon(self, addon_id: str, flavor_dir: str) -> None:
        if self._wow_running():
            raise RuntimeError("World of Warcraft is running; close it before changing addons")
        wow = self._wow_state()
        flavor = next((f for f in (wow or {}).get("flavors", []) if f["dir"] == flavor_dir), None)
        state: dict[str, Any] = dict(self.settings.get("wow_addons", {}))
        per_flavor = dict(state.get(flavor_dir, {}))
        record = per_flavor.pop(addon_id, None)
        if flavor and record:
            for folder in record.get("folders", []):
                shutil.rmtree(os.path.join(flavor["addons_dir"], folder), ignore_errors=True)
        state[flavor_dir] = per_flavor
        self.settings["wow_addons"] = state
        self._save_settings()

    details_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _parse_date(value: Any) -> Optional[int]:
        """ISO date or datetime ("2026-10-13", "2026-10-21T09:00:00Z") to epoch seconds."""
        text = str(value or "")[:19]
        for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return int(time.mktime(time.strptime(text[: 19 if "T" in pattern else 10], pattern)))
            except ValueError:
                continue
        return None

    _IMAGE_URL = re.compile(r"https?://[^\s\]\[<>\"']+\.(?:png|jpe?g|gif|webp)(?:\?[^\s\]\[<>\"']*)?", re.I)

    @classmethod
    def _extract_images(cls, markup: str) -> list[str]:
        """Image links from [img] tags, <img> tags, markdown images and bare URLs, in order, without duplicates."""
        found: list[str] = []
        for match in re.finditer(r"\[img[^\]]*\](.*?)\[/img\]|<img[^>]+src=[\"']([^\"']+)[\"']|!\[[^\]]*\]\(([^)]+)\)", markup or "", flags=re.I | re.S):
            url = next((group for group in match.groups() if group), "").strip()
            if url and url not in found:
                found.append(url)
        for match in cls._IMAGE_URL.finditer(markup or ""):
            if match.group(0) not in found:
                found.append(match.group(0))
        return found[:6]

    @classmethod
    def _plain_text(cls, markup: str, limit: int) -> str:
        """HTML/BBCode/Markdown-ish text to something readable in a dialog, with image links removed."""
        text = re.sub(r"\[img[^\]]*\].*?\[/img\]", "", markup or "", flags=re.I | re.S)
        text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # markdown links -> their text
        text = cls._IMAGE_URL.sub("", text)
        text = re.sub(r"\[/?(?:b|i|u|url|img|list|\*|color|size|quote)[^\]]*\]", "", text, flags=re.I)
        text = re.sub(r"^#+\s*", "", text, flags=re.M)
        text = re.sub(r"[*_`]{1,3}", "", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:limit].rstrip() + ("…" if len(text) > limit else "")

    def _addon_details_sync(self, addon_id: str) -> dict[str, Any]:
        cached = self.details_cache.get(addon_id)
        if cached and time.time() - cached["fetched_at"] < ADDON_CACHE_TTL:
            return cached["data"]
        addon = self._addon(addon_id)
        if not addon and addon_id.startswith("wowi:"):
            entry = self._wowi_entries().get(addon_id.split(":", 1)[1], {})
            addon = wowi_addon_entry(addon_id.split(":", 1)[1], str(entry.get("UIName", "")), str(entry.get("UIAuthorName", "")))
        if not addon:
            raise RuntimeError("Unknown addon")
        data: dict[str, Any] = {"id": addon_id, "name": addon["name"], "source": addon["source"], "author": "", "version": "", "updated": None,
                                "downloads": None, "description": addon.get("summary", ""), "changes": "", "url": "", "images": []}
        if addon["source"] == "wowi":
            uid = addon["uid"]
            entry = self._wowi_entries().get(str(uid), {})
            details = json.loads(self._http_get(f"{WOWI_API}/filedetails/{uid}.json"))
            detail = details[0] if isinstance(details, list) and details else {}
            data.update({
                "author": str(detail.get("UIAuthorName") or entry.get("UIAuthorName") or ""),
                "version": str(detail.get("UIVersion") or ""),
                "updated": int(entry.get("UIDate") or 0) // 1000 or None,
                "downloads": int(entry.get("UIDownloadTotal") or 0) or None,
                "description": self._plain_text(str(detail.get("UIDescription") or ""), 1200),
                "changes": self._plain_text(str(detail.get("UIChangeLog") or ""), 600),
                "url": f"https://www.wowinterface.com/downloads/info{uid}",
                "images": self._extract_images(str(detail.get("UIDescription") or "")),
            })
        elif addon["source"] == "github":
            repo = json.loads(self._http_get(f"{GITHUB_API}/repos/{addon['repo']}", {"Accept": "application/vnd.github+json"}))
            latest = self._addon_latest(addon)
            newest = latest["releases"][0] if latest["releases"] else {}
            data.update({
                "author": str(repo.get("owner", {}).get("login") or ""),
                "version": str(newest.get("version") or ""),
                "updated": self._parse_date(newest.get("published")),
                "downloads": int(repo.get("stargazers_count") or 0) or None,
                "description": (addon.get("summary") or "") + ("\n\n" + str(repo.get("description")) if repo.get("description") else ""),
                "changes": self._plain_text(str(newest.get("notes") or ""), 700),
                "url": str(repo.get("html_url") or f"https://github.com/{addon['repo']}"),
                "downloads_label": "stars",
                "images": self._extract_images(str(newest.get("notes") or "")),
            })
        elif addon["source"] == "tukui":
            info = json.loads(self._http_get(f"{TUKUI_API}/addon/{addon['slug']}"))
            data.update({
                "author": str(info.get("author") or ""), "version": str(info.get("version") or ""),
                "updated": self._parse_date(info.get("last_update")),
                "url": str(info.get("web_url") or "https://tukui.org/elvui"),
            })
        self.details_cache[addon_id] = {"fetched_at": time.time(), "data": data}
        return data

    async def addon_details(self, addon_id: str) -> dict[str, Any]:
        """Author, version, dates, description and recent changes for the details sheet."""
        return await asyncio.to_thread(self._addon_details_sync, addon_id)

    async def addon_update_summary(self) -> dict[str, Any]:
        """
        Per-flavour count of managed addons with an update available, for the
        panel. Uses the release cache, so it is cheap after the first check.
        """
        listing = await self.list_addons()
        summary: dict[str, Any] = {}
        for flavor in (listing["wow"] or {}).get("flavors", []):
            pending = [row["name"] for row in listing["addons"] if row["flavors"].get(flavor["dir"], {}).get("update_available")]
            summary[flavor["dir"]] = {"updates": len(pending), "names": pending}
        return {"flavors": summary, "game_running": listing.get("game_running", False)}

    async def update_addons(self, flavor_dir: str) -> dict[str, Any]:
        """Reinstall every addon in a flavour whose latest version differs. Returns {updated: [...], failed: {id: error}}."""
        listing = await self.list_addons()
        updated: list[str] = []
        failed: dict[str, str] = {}
        for row in listing["addons"]:
            status = row["flavors"].get(flavor_dir)
            if status and status["update_available"]:
                try:
                    await self.install_addon(row["id"], flavor_dir)
                    updated.append(row["id"])
                except Exception as error:
                    failed[row["id"]] = str(error)[:200]
        return {"updated": updated, "failed": failed}

    # ------------------------------------------------------------------ artwork

    def _sgdb_get(self, path: str, params: Optional[dict[str, str]] = None) -> Any:
        key = self.settings.get("sgdb_api_key")
        if not key:
            raise RuntimeError("No SteamGridDB API key set")
        url = SGDB_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        payload = json.loads(self._http_get(url, {"Authorization": f"Bearer {key}"}))
        if not payload.get("success"):
            raise RuntimeError(f"SteamGridDB: {payload.get('errors')}")
        return payload.get("data")

    def _sgdb_find_game(self, name: str, steam_app_id: Optional[int]) -> Optional[int]:
        if steam_app_id:
            try:
                return int(self._sgdb_get(f"/games/steam/{steam_app_id}")["id"])
            except Exception as error:
                decky.logger.info("SteamGridDB steam-id lookup failed for %s: %s", name, error)
        results = self._sgdb_get("/search/autocomplete/" + urllib.parse.quote(name)) or []
        for result in results:
            if str(result.get("name", "")).lower() == name.lower():
                return int(result["id"])
        return int(results[0]["id"]) if results else None

    def _sgdb_asset(self, game_id: int, kind: str) -> Optional[tuple[bytes, str]]:
        """Download the top-ranked asset of a kind. Returns (bytes, mime) or None."""
        path, params = SGDB_ASSET_QUERIES[kind]
        try:
            items = self._sgdb_get(path.format(id=game_id), params) or []
        except Exception as error:
            decky.logger.info("SteamGridDB %s query failed: %s", kind, error)
            return None
        for item in items:
            url, mime = item.get("url"), str(item.get("mime", ""))
            if not url:
                continue
            try:
                data = self._http_get(url, timeout=60)
                if len(data) > MAX_ASSET_BYTES and item.get("thumb"):
                    data = self._http_get(item["thumb"], timeout=60)
                return data, mime
            except Exception as error:
                decky.logger.info("SteamGridDB %s download failed: %s", kind, error)
        return None

    def _fetch_artwork_sync(self, name: str, launch_code: str, steam_app_id: Optional[int]) -> dict[str, Any]:
        result: dict[str, Any] = {"source": "none", "assets": {}, "icon_path": None}
        if self.settings.get("sgdb_api_key"):
            try:
                game_id = SGDB_BATTLENET_ID if launch_code == "bnet" else self._sgdb_find_game(name, steam_app_id)
                if game_id:
                    for kind in SGDB_ASSET_QUERIES:
                        found = self._sgdb_asset(game_id, kind)
                        if not found:
                            continue
                        data, mime = found
                        if kind == "icon":
                            ext = "png" if "png" in mime else "ico"
                            icon_dir = os.path.join(self._download_dir(), "icons")
                            os.makedirs(icon_dir, exist_ok=True)
                            icon_path = os.path.join(icon_dir, f"{launch_code}.{ext}")
                            with open(icon_path, "wb") as handle:
                                handle.write(data)
                            result["icon_path"] = icon_path
                        else:
                            result["assets"][kind] = {
                                "b64": base64.b64encode(data).decode("ascii"),
                                "ext": "png" if "png" in mime else "jpg",
                            }
                    if result["assets"] or result["icon_path"]:
                        result["source"] = "sgdb"
                        return result
            except Exception as error:
                decky.logger.warning("SteamGridDB artwork failed for %s: %s", name, error)
        if steam_app_id:
            for kind, (filename, ext) in STEAM_CDN_ASSETS.items():
                try:
                    data = self._http_get(f"{STEAM_CDN}/{steam_app_id}/{filename}", timeout=60)
                    result["assets"][kind] = {"b64": base64.b64encode(data).decode("ascii"), "ext": ext}
                except Exception as error:
                    decky.logger.info("Steam CDN %s missing for %s: %s", kind, name, error)
            if result["assets"]:
                result["source"] = "steam"
        return result

    async def fetch_artwork(self, name: str, launch_code: str, steam_app_id: Optional[int] = None) -> dict[str, Any]:
        """
        Collect library artwork for one shortcut: capsule, header, hero and logo
        as base64 for `SetCustomArtworkForApp`, plus an icon file path for
        `SetShortcutIcon`. SteamGridDB first (needs the user's key), then Valve's
        CDN for titles that are also on Steam. Runs off the event loop because
        it downloads several images.
        """
        return await asyncio.to_thread(self._fetch_artwork_sync, name, launch_code, steam_app_id)

    # ------------------------------------------------------------------ lifecycle

    async def _main(self) -> None:
        self.download_progress = 0.0
        self.download_total = 0
        self.download_error = None
        self.download_thread = None
        self._load_settings()
        decky.logger.info("FrostDeck backend started (steam root: %s)", _steam_root())

    async def _unload(self) -> None:
        decky.logger.info("FrostDeck backend stopping")

    async def _uninstall(self) -> None:
        decky.logger.info(
            "FrostDeck uninstalled. Battle.net, games, the Proton prefix and the Steam shortcuts are left in place; "
            "game shortcuts keep working via ~/.local/share/FrostDeck/bnet-launch.sh"
        )

    async def _migration(self) -> None:
        """
        The plugin shipped briefly under the name BlizzardDeck. Decky keys its
        settings, data and log folders by plugin name, so carry those over, then
        make the next panel open rewrite shortcuts (the launch helper moved with
        the name) and re-apply artwork (shortcut icons lived in the data folder).
        """
        old_settings = os.path.join(decky.DECKY_HOME, "settings", "BlizzardDeck")
        old_data = os.path.join(decky.DECKY_HOME, "data", "BlizzardDeck")
        old_logs = os.path.join(decky.DECKY_HOME, "logs", "BlizzardDeck")
        migrated = False
        try:
            if os.path.isdir(old_settings):
                decky.migrate_settings(old_settings)
                migrated = True
            if os.path.isdir(old_data):
                decky.migrate_runtime(old_data)
                migrated = True
            if os.path.isdir(old_logs):
                decky.migrate_logs(old_logs)
        except Exception as error:
            decky.logger.warning("Migration from BlizzardDeck folders failed: %s", error)
        if migrated:
            self._load_settings()
            self.settings["shortcut_schema"] = 0
            self.settings["artwork_applied"] = {}
            self._save_settings()
            decky.logger.info("Migrated settings and data from BlizzardDeck")
