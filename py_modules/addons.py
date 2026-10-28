"""
World of Warcraft addon catalog and helpers.

Addons are plain folders under `<flavour>/Interface/AddOns`. Everything here
comes from sources that publish deterministic packages:

* GitHub releases built with the BigWigs packager, which attach a `release.json`
  manifest naming a zip per game flavour (`mainline`, `mists`, `classic`,
  `forever`, ...), each with the interface version it targets.
* The Tukui API, for ElvUI.

Which zip fits which installed flavour is decided by interface version: the
installed client's version (from `.build.info`) is turned into an interface
number (12.1.0 -> 120100) and matched to the closest entry with the same major.
"""

import os
import re
from typing import Any, Optional, TypedDict


class AddonEntry(TypedDict, total=False):
    id: str
    name: str
    summary: str
    source: str        # "github" | "tukui" | "wowi"
    repo: str          # github: owner/name
    slug: str          # tukui: addon slug
    uid: str           # wowi: WoWInterface file id
    featured: bool
    category: str
    folder: str        # main AddOns folder the package creates; used to adopt hand-installed copies
    custom: bool       # added by the user as owner/repo rather than shipped in the catalog


ADDON_CATALOG: list[AddonEntry] = [
    {"id": "consoleport", "folder": "ConsolePort", "name": "ConsolePort", "source": "github", "repo": "seblindfors/ConsolePort", "featured": True, "category": "Controller",
     "summary": "Full gamepad support: radial menus, targeting, cursor, UI navigation. The addon that makes WoW playable on a Steam Deck."},
    {"id": "immersion", "folder": "Immersion", "name": "Immersion", "source": "github", "repo": "seblindfors/Immersion", "category": "Controller",
     "summary": "Replaces quest and gossip windows with a cinematic dialogue view that works well with a controller."},
    {"id": "weakauras", "folder": "WeakAuras", "name": "WeakAuras", "source": "github", "repo": "WeakAuras/WeakAuras2", "category": "Combat",
     "summary": "Custom on-screen displays for buffs, cooldowns and mechanics. GitHub builds currently cover the Classic flavours only."},
    {"id": "dbm", "folder": "DBM-Core", "name": "Deadly Boss Mods", "source": "github", "repo": "DeadlyBossMods/DeadlyBossMods", "category": "Combat",
     "summary": "Boss encounter timers and warnings for dungeons and raids."},
    {"id": "bigwigs", "folder": "BigWigs", "name": "BigWigs", "source": "github", "repo": "BigWigsMods/BigWigs", "category": "Combat",
     "summary": "Lightweight raid boss timers and alerts."},
    {"id": "littlewigs", "folder": "LittleWigs", "name": "LittleWigs", "source": "github", "repo": "BigWigsMods/LittleWigs", "category": "Combat",
     "summary": "BigWigs modules for dungeons, delves and world bosses."},
    {"id": "capping", "folder": "Capping", "name": "Capping", "source": "github", "repo": "BigWigsMods/Capping", "category": "PvP",
     "summary": "Battleground timers and objectives."},
    {"id": "plater", "folder": "Plater", "name": "Plater Nameplates", "source": "github", "repo": "Tercioo/Plater-Nameplates", "category": "Interface",
     "summary": "Highly configurable enemy and friendly nameplates."},
    {"id": "bartender4", "folder": "Bartender4", "name": "Bartender4", "source": "github", "repo": "Nevcairiel/Bartender4", "category": "Interface",
     "summary": "Action bar replacement with full layout control."},
    {"id": "tellmewhen", "folder": "TellMeWhen", "name": "TellMeWhen", "source": "github", "repo": "ascott18/TellMeWhen", "category": "Combat",
     "summary": "Icon-based cooldown, buff and proc tracking."},
    {"id": "masque", "folder": "Masque", "name": "Masque", "source": "github", "repo": "SFX-WoW/Masque", "category": "Interface",
     "summary": "Skinning engine for action buttons used by many other addons."},
    {"id": "aio", "folder": "AdvancedInterfaceOptions", "name": "Advanced Interface Options", "source": "github", "repo": "Stanzilla/AdvancedInterfaceOptions", "category": "Interface",
     "summary": "Brings back interface options Blizzard removed from the settings menu."},
    {"id": "mdt", "folder": "MythicDungeonTools", "name": "Mythic Dungeon Tools", "source": "github", "repo": "Nnoggie/MythicDungeonTools", "category": "Dungeons",
     "summary": "Mythic+ route planning and dungeon maps."},
    {"id": "molinari", "folder": "Molinari", "name": "Molinari", "source": "github", "repo": "p3lim-wow/Molinari", "category": "Quality of life",
     "summary": "One-click milling, prospecting, disenchanting and lockpicking."},
    {"id": "questie", "folder": "Questie", "name": "Questie", "source": "github", "repo": "Questie/Questie", "category": "Classic",
     "summary": "Quest helper for Classic: objectives on the map and minimap."},
    {"id": "elvui", "folder": "ElvUI", "name": "ElvUI", "source": "tukui", "slug": "elvui", "category": "Interface",
     "summary": "Complete user interface replacement."},
]

PRODUCT_LABELS: dict[str, str] = {
    "wow": "Retail",
    "wow_classic": "Classic",
    "wow_classic_era": "Classic Era",
    "wow_forever": "Forever",
    "wowf": "Forever",
}


def addon_by_id(addon_id: str, extra: Optional[list[AddonEntry]] = None) -> Optional[AddonEntry]:
    for entry in list(ADDON_CATALOG) + list(extra or []):
        if entry["id"] == addon_id:
            return entry
    return None


def wowi_addon_entry(uid: str, name: str, author: str = "") -> AddonEntry:
    """Catalog entry for an addon installed from WoWInterface (api.mmoui.com)."""
    return {"id": f"wowi:{uid}", "name": name, "source": "wowi", "uid": uid, "category": "WoWInterface",
            "summary": f"By {author} on WoWInterface" if author else "From WoWInterface", "custom": True}


def wowi_compatible(compat: Optional[list[dict[str, Any]]], version: str) -> Optional[bool]:
    """
    WoWInterface lists supported game versions per addon ("11.0.2", "1.15.2", ...).
    Same rule as the GitHub manifests: a shared major version counts. None when
    the addon lists nothing, which the UI shows as unknown rather than unsupported.
    """
    if not compat:
        return None
    try:
        major = int(version.split(".")[0])
    except (ValueError, IndexError):
        return None
    for item in compat:
        try:
            if int(str(item.get("version", "")).split(".")[0]) == major:
                return True
        except ValueError:
            continue
    return False


def custom_addon_entry(repo: str) -> AddonEntry:
    """Catalog entry for a user-supplied GitHub repo ("owner/name")."""
    name = repo.split("/", 1)[1] if "/" in repo else repo
    return {"id": f"gh:{repo.lower()}", "name": name, "source": "github", "repo": repo, "category": "Custom",
            "summary": f"From github.com/{repo}", "custom": True}


_COLOR_CODE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T.*?\|t")


def parse_toc(text: str) -> dict[str, str]:
    """`## Key: value` metadata lines from an addon's .toc, with WoW colour codes stripped from the title."""
    meta: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("##"):
            continue
        body = line[2:].strip()
        if ":" not in body:
            continue
        key, value = body.split(":", 1)
        meta[key.strip()] = _COLOR_CODE.sub("", value.strip())
    return meta


def read_addon_toc(folder_path: str) -> dict[str, str]:
    """Metadata of the folder's .toc. Prefers the plain <Folder>.toc, else any flavour-suffixed one."""
    name = os.path.basename(folder_path)
    candidates = [os.path.join(folder_path, f"{name}.toc")]
    try:
        candidates += sorted(
            os.path.join(folder_path, entry) for entry in os.listdir(folder_path) if entry.lower().endswith(".toc")
        )
    except OSError:
        return {}
    for candidate in candidates:
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                return parse_toc(handle.read())
        except OSError:
            continue
    return {}


def toc_interfaces(folder_path: str) -> list[int]:
    """
    Every interface version the folder's .toc files declare. Modern TOCs list
    several on one line ("## Interface: 120100, 50504, 11509"); older packages
    use one TOC per flavour with a suffix. Either way the numbers are what count.
    """
    found: set[int] = set()
    try:
        names = [entry for entry in os.listdir(folder_path) if entry.lower().endswith(".toc")]
    except OSError:
        return []
    for name in names:
        try:
            with open(os.path.join(folder_path, name), "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("##") and "interface" in line.lower().split(":", 1)[0]:
                        for part in line.split(":", 1)[1].split(","):
                            digits = "".join(ch for ch in part if ch.isdigit())
                            if digits:
                                found.add(int(digits))
                        break
        except OSError:
            continue
    return sorted(found)


def package_fits(addons_dir: str, folders: list[str], interface: Optional[int]) -> Optional[bool]:
    """
    Whether an unpacked package declares support for the installed interface
    major. None when the TOCs declare nothing usable (old or odd packages).
    """
    if interface is None:
        return None
    declared: set[int] = set()
    for folder in folders:
        declared.update(toc_interfaces(os.path.join(addons_dir, folder)))
    if not declared:
        return None
    return any(value // 10000 == interface // 10000 for value in declared)


def group_folders(folders: list[str]) -> dict[str, list[str]]:
    """
    Group AddOns folders into addons: "ConsolePort_Bar" belongs with "ConsolePort"
    when that folder exists, "DBM-Core" with "DBM-Core" only, etc.
    """
    present = set(folders)
    groups: dict[str, list[str]] = {}
    for folder in sorted(folders):
        key = folder
        for separator in ("_", "-"):
            if separator in folder:
                prefix = folder.split(separator, 1)[0]
                if prefix in present and prefix != folder:
                    key = prefix
                    break
        if key == folder:
            # "WeakAurasOptions" belongs with "WeakAuras": the longest other folder that prefixes this one.
            owners = [other for other in present if other != folder and folder.startswith(other)]
            if owners:
                key = max(owners, key=len)
        groups.setdefault(key, []).append(folder)
    return groups


def product_label(product: str) -> str:
    if product in PRODUCT_LABELS:
        return PRODUCT_LABELS[product]
    return product.removeprefix("wow_").replace("_", " ").title() or product


def version_to_interface(version: str) -> Optional[int]:
    """'12.1.0.65344' -> 120100; '1.15.9.12345' -> 11509."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major * 10000 + minor * 100 + patch


def parse_build_info(text: str) -> dict[str, str]:
    """
    Parse WoW's `.build.info` into {product: version}. It is a pipe-separated
    table whose header cells look like `Version!STRING:0`.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return {}
    header = [cell.split("!", 1)[0].strip() for cell in lines[0].split("|")]
    try:
        version_index = header.index("Version")
        product_index = header.index("Product")
    except ValueError:
        return {}
    versions: dict[str, str] = {}
    for line in lines[1:]:
        cells = line.split("|")
        if len(cells) > max(version_index, product_index):
            versions[cells[product_index].strip()] = cells[version_index].strip()
    return versions


def parse_flavor_info(text: str) -> Optional[str]:
    """`.flavor.info` is a two-line table: a header, then the product name."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[1] if len(lines) >= 2 else None


def discover_flavors(install_dir: str) -> list[dict[str, Any]]:
    """
    Installed WoW flavours: every `_name_` folder with a `.flavor.info`, paired
    with its version from the root `.build.info`.
    """
    versions: dict[str, str] = {}
    try:
        with open(os.path.join(install_dir, ".build.info"), "r", encoding="utf-8", errors="replace") as handle:
            versions = parse_build_info(handle.read())
    except OSError:
        pass
    flavors: list[dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(install_dir))
    except OSError:
        return flavors
    for entry in entries:
        folder = os.path.join(install_dir, entry)
        if not (entry.startswith("_") and entry.endswith("_") and os.path.isdir(folder)):
            continue
        product = None
        try:
            with open(os.path.join(folder, ".flavor.info"), "r", encoding="utf-8", errors="replace") as handle:
                product = parse_flavor_info(handle.read())
        except OSError:
            continue
        if not product:
            continue
        version = versions.get(product, "")
        flavors.append({
            "dir": entry,
            "path": folder,
            "product": product,
            "label": product_label(product),
            "version": version,
            "interface": version_to_interface(version),
            "addons_dir": os.path.join(folder, "Interface", "AddOns"),
        })
    return flavors


def pick_release(manifest: dict[str, Any], interface: Optional[int]) -> Optional[dict[str, Any]]:
    """
    From a packager `release.json`, pick the release (zip) that best fits an
    installed interface version: same major, closest number, libraries bundled.
    Returns {"filename", "flavor", "interface"} or None when nothing fits.
    """
    if interface is None:
        return None
    best: Optional[tuple[int, int, dict[str, Any]]] = None
    for release in manifest.get("releases", []):
        nolib_penalty = 1 if release.get("nolib") else 0
        for meta in release.get("metadata", []):
            target = int(meta.get("interface") or 0)
            if target // 10000 != interface // 10000:
                continue
            score = (nolib_penalty, abs(target - interface))
            if best is None or score < (best[0], best[1]):
                best = (score[0], score[1], {"filename": release.get("filename"), "flavor": meta.get("flavor"), "interface": target})
    return best[2] if best else None


def tukui_supports(patches: list[str], version: str) -> bool:
    """ElvUI lists supported game versions like '12.1.0'; match on major.minor."""
    prefix = ".".join(version.split(".")[:2])
    return any(".".join(str(p).split(".")[:2]) == prefix for p in patches)
