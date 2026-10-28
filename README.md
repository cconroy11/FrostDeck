# FrostDeck

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that gets Battle.net onto a Steam Deck and keeps your Blizzard games one tap away in Gaming Mode.

The panel is four collapsible sections, each showing a one-line summary when closed: **Battle.net** (status, open, Proton build, repair), **Games** (one row per installed game with Play or Add to Steam and a menu for the rest), **World of Warcraft** (one row per installed flavour with addon count and controller support), and **Settings** (artwork key, reset).

From the Quick Access menu you can:

1. **Install Battle.net.** The plugin downloads Blizzard's official installer, adds a `Battle.net` non-Steam shortcut running under Proton, files it in a **Blizzard** collection, and launches the installer for you. Sign in like you would on Windows.
2. **Add games to Steam.** Once you've installed games inside Battle.net, the plugin detects them and adds one shortcut per game (World of Warcraft, Diablo IV, Hearthstone, …) to the same Blizzard collection. Each shortcut boots Battle.net straight into that game.
3. **Play.** Launch from the plugin, from the Blizzard collection, or from anywhere else in your library. Controller input, the on-screen keyboard and suspend/resume all work because Steam owns the process.

## How it works

Steam, not the plugin, runs Battle.net. That is deliberate.

- The Python backend (`main.py`) only touches files: it downloads the installer to Decky's runtime folder, remembers which shortcut owns the Proton prefix, and reads Battle.net's own `Battle.net.config` inside that prefix to see which games are installed.
- The React frontend (`src/`) uses the Steam client API to create shortcuts, pick a Proton build, manage the Blizzard collection and launch apps.
- Every game shortcut shares the Battle.net prefix (`STEAM_COMPAT_DATA_PATH`), so there is one login and one install.
- Launching a game is a two-step dance, because the current Battle.net client ignores `--exec="launch <code>"` on a cold start but honours it when a second copy forwards it over IPC to a running instance. `defaults/bnet-launch.sh` runs inside the Steam/Proton session for the shortcut: it starts the client, waits until Battle.net's own log reports its main window, then runs a second `Battle.net.exe --exec="launch <code>"`. Steam keeps tracking the session until the game exits.

The Battle.net shortcut starts out pointing at the installer. After the installer finishes, the plugin notices the client in the prefix and silently repoints the same shortcut at `Battle.net Launcher.exe`.

### Quit games from inside the game

Blizzard games write their settings on a clean exit: WoW's `Config.wtf`, saved variables and controller config land only when you leave through the game's own Escape → Exit Game. Steam's Exit Game ends the process immediately and those changes are lost.

### Battle.net settings worth setting

In Battle.net → Settings → App: "On game launch" → **Exit Battle.net completely**, and "When clicking X" → **Exit Battle.net completely**. Then Steam sees the game end cleanly and returns to the library.

## Installing on a Deck from a release

1. Download `FrostDeck.zip` from the latest release at https://github.com/cconroy11/FrostDeck/releases.
2. In Decky's settings on the Deck, enable Developer Mode, then under Developer use **Install Plugin from ZIP** (or copy the zip to the Deck and unzip it into `/home/deck/homebrew/plugins/`).
3. Restart Decky from its settings, or reboot.

Once FrostDeck is in the Decky store this step becomes a tap in the store.

## Building

Requires Node 16.14+ and pnpm 9.

```sh
pnpm i
pnpm run build        # bundles src/ into dist/index.js
pnpm run typecheck    # tsc --noEmit
```

## Installing on a Deck

1. On the Deck, open Decky settings, enable **Developer Mode**, and set an SSH password in Konsole with `passwd` if you haven't already.
2. From this folder:

   ```sh
   DECK_HOST=deck@<deck-ip> ./deploy.sh
   ```

   That builds, copies the plugin to `/home/deck/homebrew/plugins/FrostDeck`, and restarts Decky.
3. Open the Quick Access menu, pick FrostDeck, and press **Install Battle.net**.

Logs land in `/home/deck/homebrew/logs/FrostDeck/` on the Deck. Each game launch also writes a short trace to `~/.local/share/FrostDeck/bnet-launch.log`, and Battle.net's own logs are under the prefix at `drive_c/users/steamuser/AppData/Local/Battle.net/Logs/`.

The deploy script stops Decky while it swaps the plugin files and verifies the copy, because Decky takes ownership of the plugins folder as root whenever it starts.

## World of Warcraft addons

Once WoW is installed through Battle.net, the panel grows a **World of Warcraft** section with the installed flavours (Retail, Classic, Classic Era, …), a one-tap **Install ConsolePort** button, and **Manage addons**, which opens a full-page manager. Addons are installed straight into `<flavour>/Interface/AddOns` inside the Proton prefix, per flavour, with update detection and one-tap Update all.

The manager also lists what is already in each AddOns folder but wasn't installed by the plugin, grouped into addons with their `.toc` title and version; known ones can be taken over with one tap so they get update checks. Any other GitHub-hosted addon can be added by repo name (`owner/name`) as long as its releases are built with the standard packager. The panel shows pending update counts per flavour with an Update all button, and nothing is changed while WoW is running.

The addons page has a search box covering the shipped catalog, your added repos and WoWInterface's public catalog (about 8,000 addons, no key needed); results install with one tap. WoWInterface's own compatibility lists lag behind patches, so they are shown as a hint only: every package, from any source, is checked against the flavour's version using the `## Interface:` lines in its `.toc` files before anything on disk is replaced, and a mismatch is refused with a plain message.

Sources are deterministic on purpose: GitHub releases built with the BigWigs packager (their `release.json` names the zip for each flavour and the interface version it targets) and the Tukui API for ElvUI. The right build for a flavour is chosen by matching the installed client's version from `.build.info` to the manifest's interface numbers, so new flavours such as WoW: Forever work as soon as authors publish for them. Addons that exist only on CurseForge are not listed. The catalog lives in `py_modules/addons.py`.

## Artwork

With a SteamGridDB API key saved in **Settings** (free: steamgriddb.com → Profile → Preferences → API), every shortcut gets a capsule, header, hero, logo and icon the moment it is added, and the Battle.net shortcut gets the launcher's own art. The key is stored only in the plugin's settings file on the Deck. Without a key, titles that are also sold on Steam (Diablo IV, Overwatch 2, …) fall back to Valve's official art from Steam's CDN. **Settings → Fetch artwork for all** re-applies art to everything.

## Proton

Steam owns Proton; the plugin never downloads or manages builds. It discovers what is installed the same way Steam does (official builds under `steamapps/common`, community builds such as GE-Proton under `compatibilitytools.d`), because the Steam client API calls that used to list them no longer exist on current Steam builds. The first time Battle.net is installed it picks a GE-Proton build if one is present, otherwise Proton Experimental, otherwise the newest numbered Proton, and pins that on every shortcut so they all share one build. Until something is pinned, Steam's own default for other titles applies and the panel says so.

**Battle.net → Proton version** lists the installed builds and changes all shortcuts together, after a confirmation. Steam is the source of truth: if you change the Battle.net shortcut's tool in Steam's Properties page instead, the plugin notices on its next refresh and new game shortcuts follow it. Stick with one build; switching mid-stream is the most common way Battle.net installs break, and switching back is the fix.

## Adding games to the catalog

`py_modules/catalog.py` maps Battle.net product codes to display names and launch codes. If a game shows up as "Unknown product", add its code there. The `Games` object inside `Battle.net.config` is the source of the keys.

## Removing things

Three different actions, from mild to total:

- **Remove from Steam only** (a game's "…" menu) deletes just the Steam shortcut. The game stays installed and can be added again any time.
- **Uninstall from Deck** (same menu) deletes the game's folder inside the prefix and its shortcut, after a confirmation that shows the size. That is what Battle.net's own uninstall does; the client offers to install it again, and settings and saves kept under the prefix's user folder survive.
- **Settings → Reset → Delete everything** removes the whole Proton prefix: Battle.net, every game, every shortcut.

Uninstalling the plugin itself removes none of the above. Battle.net, the games and the shortcuts are ordinary files and Steam entries, and game shortcuts keep launching because the small helper they use lives in `~/.local/share/FrostDeck`, outside Decky's folders. Reinstalling the plugin later picks everything back up.

## Reset

**Battle.net → Repair shortcuts** rewrites every shortcut from the current state; the plugin also does this automatically once after an update that changes the launch format. **Settings → Reset FrostDeck** removes the shortcuts this plugin made. The "Delete everything" option also wipes the Proton prefix, which uninstalls Battle.net and every game in it.
