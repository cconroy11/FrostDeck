"""
Catalog of Battle.net products.

Battle.net identifies a product two ways:

* The key used in `Battle.net.config` under `Games` (e.g. `wow`, `fenris`).
* The "launch code" accepted by `Battle.net.exe --exec="launch <CODE>"`
  (e.g. `WoW`, `Fen`). This is what a Steam shortcut needs.

Most config keys are just the lowercase launch code. The exceptions are listed
in `CONFIG_KEY_OVERRIDES`. Add new games here as Blizzard ships them.
"""

from typing import Optional, TypedDict


class CatalogEntry(TypedDict):
    name: str
    launch_code: str


GAME_CATALOG: dict[str, CatalogEntry] = {
    "WoW": {"name": "World of Warcraft", "launch_code": "WoW"},
    "WoWC": {"name": "World of Warcraft Classic", "launch_code": "WoWC"},
    "D1": {"name": "Diablo", "launch_code": "D1"},
    "OSI": {"name": "Diablo II: Resurrected", "launch_code": "OSI"},
    "D3": {"name": "Diablo III", "launch_code": "D3"},
    "Fen": {"name": "Diablo IV", "launch_code": "Fen"},
    "ANBS": {"name": "Diablo Immortal", "launch_code": "ANBS"},
    "WTCG": {"name": "Hearthstone", "launch_code": "WTCG"},
    "Hero": {"name": "Heroes of the Storm", "launch_code": "Hero"},
    "Pro": {"name": "Overwatch 2", "launch_code": "Pro"},
    "S1": {"name": "StarCraft: Remastered", "launch_code": "S1"},
    "S2": {"name": "StarCraft II", "launch_code": "S2"},
    "W1": {"name": "Warcraft: Orcs & Humans", "launch_code": "W1"},
    "W1R": {"name": "Warcraft I: Remastered", "launch_code": "W1R"},
    "W2": {"name": "Warcraft II: Battle.net Edition", "launch_code": "W2"},
    "W2R": {"name": "Warcraft II: Remastered", "launch_code": "W2R"},
    "W3": {"name": "Warcraft III: Reforged", "launch_code": "W3"},
    "WoWF": {"name": "World of Warcraft: Forever", "launch_code": "WoWF"},
    "GRY": {"name": "Warcraft Rumble", "launch_code": "GRY"},
    "RTRO": {"name": "Blizzard Arcade Collection", "launch_code": "RTRO"},
    "VIPR": {"name": "Call of Duty: Black Ops 4", "launch_code": "VIPR"},
    "ODIN": {"name": "Call of Duty: Modern Warfare", "launch_code": "ODIN"},
    "LAZR": {"name": "Call of Duty: MW2 Campaign Remastered", "launch_code": "LAZR"},
    "ZEUS": {"name": "Call of Duty: Black Ops Cold War", "launch_code": "ZEUS"},
    "FORE": {"name": "Call of Duty: Vanguard", "launch_code": "FORE"},
    "AUKS": {"name": "Call of Duty", "launch_code": "AUKS"},
    "NINA": {"name": "Call of Duty: Modern Warfare II", "launch_code": "NINA"},
    "PNTA": {"name": "Call of Duty: Modern Warfare III", "launch_code": "PNTA"},
    "SPOT": {"name": "Call of Duty: Modern Warfare III (legacy code)", "launch_code": "SPOT"},
    "BTLR": {"name": "Call of Duty: Black Ops 6", "launch_code": "BTLR"},
    "WLBY": {"name": "Crash Bandicoot 4: It's About Time", "launch_code": "WLBY"},
    "LBRA": {"name": "Tony Hawk's Pro Skater 3 + 4", "launch_code": "LBRA"},
    "AQUA": {"name": "Avowed", "launch_code": "AQUA"},
    "ARIS": {"name": "Doom: The Dark Ages", "launch_code": "ARIS"},
    "LYRA": {"name": "The Witcher 3: Wild Hunt Remastered", "launch_code": "LYRA"},
    "SCOR": {"name": "Sea of Thieves", "launch_code": "SCOR"},
    "ARK": {"name": "The Outer Worlds 2", "launch_code": "ARK"},
}

# Battle.net.config key -> launch code, for products whose key is not simply
# the lowercase launch code.
CONFIG_KEY_OVERRIDES: dict[str, str] = {
    "prometheus": "Pro",
    "fenris": "Fen",
    "diablo3": "D3",
    "hs_beta": "WTCG",
    "heroes": "Hero",
    "gryphon": "GRY",
    "wow_classic": "WoWC",
    "wow_classic_era": "WoWC",
    "wow_forever": "WoWF",
    "aqua": "AQUA",
    "seaofthieves": "SCOR",
    "sot": "SCOR",
}

# Steam store app ids for Blizzard/Activision/Microsoft titles also sold on Steam.
# Used to look games up precisely on SteamGridDB and, without an API key, to pull
# Valve's official library art from Steam's CDN.
STEAM_APP_IDS: dict[str, int] = {
    "Fen": 2344520,    # Diablo IV
    "Pro": 2357570,    # Overwatch 2
    "SCOR": 1172620,   # Sea of Thieves
    "WLBY": 1378990,   # Crash Bandicoot 4
    "AUKS": 1938090,   # Call of Duty
    "AQUA": 2457220,   # Avowed
    "ARIS": 3017860,   # Doom: The Dark Ages
}

# SteamGridDB's own id for the Battle.net launcher entry ("Battle.net (Program)").
SGDB_BATTLENET_ID = 5248250

_LAUNCH_CODE_BY_LOWER = {code.lower(): code for code in GAME_CATALOG}


def resolve_game(config_key: str) -> Optional[CatalogEntry]:
    """Map a `Battle.net.config` product key to a catalog entry, if known."""
    key = config_key.strip()
    launch_code = CONFIG_KEY_OVERRIDES.get(key.lower()) or _LAUNCH_CODE_BY_LOWER.get(key.lower())
    if launch_code is None:
        return None
    return GAME_CATALOG.get(launch_code)
