/**
 * High-level user flows. Each one combines backend calls (files, settings)
 * with Steam client calls (shortcuts, Proton, collections, launching).
 */

import { toaster } from "@decky/api";
import {
  downloadInstaller,
  fetchArtwork,
  getDownloadStatus,
  getState,
  resetBackend,
  setArtworkApplied,
  setBnetShortcut,
  setCompatTool,
  setGameAppId,
  setShortcutSchema,
  type DetectedGame,
  type PluginState,
} from "./backend";
import {
  addShortcut,
  addToCollection,
  applyArtwork,
  getCompatToolFor,
  launchApp,
  listCompatTools,
  pickDefaultCompatTool,
  removeFromCollection,
  removeShortcut,
  setCompatToolFor,
  shortcutExists,
  updateShortcut,
} from "./steam";

export const BNET_SHORTCUT_NAME = "Battle.net";

const dirname = (path: string): string => path.slice(0, Math.max(path.lastIndexOf("/"), 0)) || "/";

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function toast(title: string, body: string): void {
  toaster.toast({ title, body });
}

/** Wait for the backend download to finish, reporting fractional progress. */
async function waitForInstaller(onProgress: (fraction: number) => void): Promise<string> {
  const start = await downloadInstaller();
  if (start.ready) {
    onProgress(1);
    return start.path;
  }
  for (;;) {
    await sleep(500);
    const status = await getDownloadStatus();
    onProgress(status.progress);
    if (status.error) {
      throw new Error(`Download failed: ${status.error}`);
    }
    if (status.ready) {
      return status.path;
    }
    if (!status.running && !status.ready) {
      throw new Error("Download stopped unexpectedly");
    }
  }
}

/**
 * Decide which Proton build to use. Honors the saved choice when Steam still
 * offers it, otherwise picks a sensible default and saves that.
 */
async function resolveCompatTool(appId: number, saved: string | null): Promise<string | null> {
  const tools = await listCompatTools(appId);
  if (saved && tools.some((tool) => tool.name === saved)) {
    return saved;
  }
  const chosen = pickDefaultCompatTool(tools);
  if (chosen) {
    await setCompatTool(chosen);
  }
  return chosen;
}

/**
 * Steam is the source of truth for the Proton build: whatever the Battle.net
 * shortcut has right now (set here, or in Steam's Properties page) is what the
 * plugin remembers and applies to game shortcuts. Returns true if it changed.
 */
export async function syncCompatToolFromSteam(state: PluginState): Promise<boolean> {
  if (!state.bnet_app_id || !shortcutExists(state.bnet_app_id)) {
    return false;
  }
  const current = await getCompatToolFor(state.bnet_app_id);
  if (!current || !current.name || current.name === state.compat_tool) {
    return false;
  }
  await setCompatTool(current.name);
  return true;
}

/** The build a new game shortcut should use: Battle.net's current one, else the saved/default pick. */
async function compatToolForNewGame(state: PluginState, appId: number): Promise<string | null> {
  if (state.bnet_app_id && shortcutExists(state.bnet_app_id)) {
    const current = await getCompatToolFor(state.bnet_app_id);
    if (current?.name) {
      return current.name;
    }
  }
  return resolveCompatTool(appId, state.compat_tool);
}

/**
 * Step 1: download the installer, create the "Battle.net" shortcut pointing
 * at it, put it under Proton and in the Blizzard collection, then launch it so
 * the user lands straight in the installer.
 */
export async function installBattleNet(
  state: PluginState,
  onProgress: (fraction: number) => void,
): Promise<void> {
  const installerPath = await waitForInstaller(onProgress);

  let appId = state.bnet_app_id;
  const spec = {
    name: BNET_SHORTCUT_NAME,
    exe: installerPath,
    startDir: dirname(installerPath),
    launchOptions: state.installer_launch_options,
  };
  if (appId && shortcutExists(appId)) {
    updateShortcut(appId, spec);
  } else {
    appId = await addShortcut(spec);
  }

  const tool = await resolveCompatTool(appId, state.compat_tool);
  if (tool) {
    setCompatToolFor(appId, tool);
  }
  await addToCollection(state.collection_name, appId);
  await setBnetShortcut(appId, "installer");
  await setShortcutSchema(state.current_shortcut_schema);

  // Give Steam a moment to persist the compat-tool choice before launching.
  await sleep(750);
  if (launchApp(appId)) {
    toast("Battle.net installer opened", "Finish the installer, sign in, then come back to FrostDeck.");
  } else {
    toast("Battle.net shortcut created", "Launch “Battle.net” from your library to run the installer.");
  }
}

/**
 * Step 2: once Battle.net.exe exists in the prefix, point the same shortcut at
 * the real launcher so the user never has to see the installer again.
 */
export async function promoteToLauncher(state: PluginState): Promise<boolean> {
  if (!state.bnet_installed || !state.bnet_app_id || !state.bnet_exe || !state.bnet_start_dir) {
    return false;
  }
  if (state.bnet_phase === "launcher") {
    return false;
  }
  updateShortcut(state.bnet_app_id, {
    name: BNET_SHORTCUT_NAME,
    exe: state.bnet_exe,
    startDir: state.bnet_start_dir,
    launchOptions: "",
  });
  await setBnetShortcut(state.bnet_app_id, "launcher");
  await applyArtworkFor(state, BNET_ART_CODE, BNET_SHORTCUT_NAME, state.bnet_app_id, null);
  toast("Battle.net is installed", "The Battle.net shortcut now opens the launcher directly.");
  return true;
}

export const BNET_ART_CODE = "bnet";

/**
 * Fetch and apply library art for one shortcut. Never throws: artwork is a
 * nicety and must not break adding or launching a game.
 */
export async function applyArtworkFor(
  state: PluginState,
  code: string,
  name: string,
  appId: number,
  steamAppId: number | null,
): Promise<number> {
  if (!state.sgdb_api_key_set && !steamAppId) {
    return 0;
  }
  try {
    const art = await fetchArtwork(name, code, steamAppId);
    if (art.source === "none") {
      return 0;
    }
    const applied = await applyArtwork(appId, art);
    if (applied > 0) {
      await setArtworkApplied(code, true);
    }
    return applied;
  } catch (error) {
    console.warn("[FrostDeck] artwork failed for", name, error);
    return 0;
  }
}

/** Shortcuts that exist but have no art yet: Battle.net itself plus every game. */
export function shortcutsMissingArtwork(state: PluginState): Array<{ code: string; name: string; appId: number; steamAppId: number | null }> {
  const missing: Array<{ code: string; name: string; appId: number; steamAppId: number | null }> = [];
  if (state.bnet_phase === "launcher" && state.bnet_app_id && shortcutExists(state.bnet_app_id) && !state.artwork_applied[BNET_ART_CODE]) {
    missing.push({ code: BNET_ART_CODE, name: BNET_SHORTCUT_NAME, appId: state.bnet_app_id, steamAppId: null });
  }
  for (const [code, appId] of Object.entries(state.game_app_ids)) {
    if (!shortcutExists(appId) || state.artwork_applied[code]) {
      continue;
    }
    const game = state.games.find((entry) => entry.launch_code === code);
    missing.push({ code, name: game?.name ?? code, appId, steamAppId: game?.steam_app_id ?? null });
  }
  return missing;
}

/** Apply art to every shortcut that lacks it (or to all of them when `force`). */
export async function fetchAllArtwork(state: PluginState, force = false): Promise<number> {
  const targets = force
    ? [
        ...(state.bnet_phase === "launcher" && state.bnet_app_id && shortcutExists(state.bnet_app_id)
          ? [{ code: BNET_ART_CODE, name: BNET_SHORTCUT_NAME, appId: state.bnet_app_id, steamAppId: null }]
          : []),
        ...Object.entries(state.game_app_ids)
          .filter(([, appId]) => shortcutExists(appId))
          .map(([code, appId]) => {
            const game = state.games.find((entry) => entry.launch_code === code);
            return { code, name: game?.name ?? code, appId, steamAppId: game?.steam_app_id ?? null };
          }),
      ]
    : shortcutsMissingArtwork(state);
  let done = 0;
  for (const target of targets) {
    if ((await applyArtworkFor(state, target.code, target.name, target.appId, target.steamAppId)) > 0) {
      done++;
    }
  }
  return done;
}

export function openBattleNet(state: PluginState): void {
  if (!state.bnet_app_id || !launchApp(state.bnet_app_id)) {
    toast("Battle.net", "Could not find the Battle.net shortcut. Try Reset in Settings.");
  }
}

/**
 * Launch options for a game shortcut.
 *
 * `%command%` expands to `<runtime entry> --verb=waitforexitandrun -- <proton>
 * waitforexitandrun <exe>`. The inline bash finds the final `waitforexitandrun`
 * token and swaps `<proton> waitforexitandrun <exe>` for the FrostDeck helper
 * (see defaults/bnet-launch.sh), which starts the client and then forwards the
 * launch command to it. Everything before that token (the Steam runtime
 * container) is kept, so the helper runs inside the same session Steam tracks.
 * bash is also what keeps Steam's own tokeniser away from the quoting. For a
 * Proton trace, add `PROTON_LOG=1 ` in front of the shortcut's launch options.
 */
export function gameLaunchOptions(state: PluginState, launchCode: string): string {
  const helper = state.launch_helper ?? "";
  const script =
    'a=("$@"); n=${#a[@]}; for ((i=n-1;i>0;i--)); do [ "${a[i]}" = waitforexitandrun ] && break; done; ' +
    'if [ "$i" -le 0 ]; then exec "$@"; fi; ' +
    `exec "\${a[@]:0:i-1}" bash "${helper}" "\${a[i-1]}" "\${a[i+1]}" ${launchCode} "${state.bnet_log_dir}" "${state.helper_log}"`;
  return `STEAM_COMPAT_DATA_PATH="${state.compatdata_dir}" bash -c '${script}' -- %command%`;
}

/**
 * Rewrite every shortcut this plugin owns from current state: exe, start dir,
 * launch options and Proton build. Runs automatically once after an update
 * that changes how shortcuts are written, and on demand from Settings.
 */
export async function repairShortcuts(state: PluginState): Promise<number> {
  if (!state.bnet_installed || !state.bnet_exe || !state.bnet_client_exe || !state.bnet_start_dir) {
    return 0;
  }
  let repaired = 0;
  if (state.bnet_app_id && shortcutExists(state.bnet_app_id) && state.bnet_phase === "launcher") {
    updateShortcut(state.bnet_app_id, {
      name: BNET_SHORTCUT_NAME,
      exe: state.bnet_exe,
      startDir: state.bnet_start_dir,
      launchOptions: "",
    });
    if (state.compat_tool) {
      setCompatToolFor(state.bnet_app_id, state.compat_tool);
    }
    repaired++;
  }
  for (const [code, appId] of Object.entries(state.game_app_ids)) {
    if (!shortcutExists(appId)) {
      continue;
    }
    const game = state.games.find((entry) => entry.launch_code === code);
    updateShortcut(appId, {
      name: game?.name,
      exe: state.bnet_client_exe,
      startDir: state.bnet_start_dir,
      launchOptions: gameLaunchOptions(state, code),
    });
    // A build chosen for this one game in Steam's Properties page is a deliberate
    // override; only shortcuts with nothing set get the shared build.
    const own = await getCompatToolFor(appId);
    if (state.compat_tool && !own?.name) {
      setCompatToolFor(appId, state.compat_tool);
    }
    repaired++;
  }
  await setShortcutSchema(state.current_shortcut_schema);
  return repaired;
}

/** Create a per-game shortcut that boots Battle.net straight into the game. */
export async function addGameShortcut(state: PluginState, game: DetectedGame): Promise<number> {
  if (!state.bnet_client_exe || !state.bnet_start_dir || !state.compatdata_dir) {
    throw new Error("Battle.net is not installed yet");
  }
  if (!state.launch_helper) {
    throw new Error("Could not install the launch helper script; see the plugin log");
  }
  const existing = state.game_app_ids[game.launch_code];
  if (existing && shortcutExists(existing)) {
    return existing;
  }
  const appId = await addShortcut({
    name: game.name,
    exe: state.bnet_client_exe,
    startDir: state.bnet_start_dir,
    launchOptions: gameLaunchOptions(state, game.launch_code),
  });
  const tool = await compatToolForNewGame(state, appId);
  if (tool) {
    setCompatToolFor(appId, tool);
  }
  await addToCollection(state.collection_name, appId);
  await setGameAppId(game.launch_code, appId);
  await applyArtworkFor(state, game.launch_code, game.name, appId, game.steam_app_id);
  return appId;
}

export async function removeGameShortcut(state: PluginState, launchCode: string): Promise<void> {
  const appId = state.game_app_ids[launchCode];
  if (appId) {
    await removeFromCollection(state.collection_name, appId);
    removeShortcut(appId);
  }
  await setGameAppId(launchCode, null);
}

export function playGame(state: PluginState, launchCode: string): boolean {
  const appId = state.game_app_ids[launchCode];
  return !!appId && launchApp(appId);
}

/** Apply a Proton build to the Battle.net shortcut and every game shortcut. */
export async function applyCompatToolEverywhere(state: PluginState, toolName: string): Promise<void> {
  const ids = [state.bnet_app_id, ...Object.values(state.game_app_ids)].filter(
    (id): id is number => typeof id === "number" && shortcutExists(id),
  );
  for (const id of ids) {
    setCompatToolFor(id, toolName); // throws with a clear message if Steam offers no setter
  }
  await setCompatTool(toolName);
}

/** Remove every shortcut this plugin created and forget all state. */
export async function resetEverything(deletePrefix: boolean): Promise<void> {
  const state = await getState();
  for (const [code, appId] of Object.entries(state.game_app_ids)) {
    await removeFromCollection(state.collection_name, appId);
    removeShortcut(appId);
    await setGameAppId(code, null);
  }
  if (state.bnet_app_id) {
    await removeFromCollection(state.collection_name, state.bnet_app_id);
    removeShortcut(state.bnet_app_id);
  }
  await resetBackend(deletePrefix);
}
