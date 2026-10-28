/**
 * Thin wrappers around the Steam client JS API that Decky exposes.
 *
 * `SteamClient`, `appStore` and `collectionStore` are globals inside the Steam
 * UI. `@decky/ui` types the first two; `collectionStore` is typed here from the
 * shape other Decky plugins rely on, with runtime guards because Valve can
 * change it without notice.
 */

import { frontendLog, getAppCompatToolFromConfig, listCompatToolsOnDisk, type Artwork, type ArtworkKind } from "./backend";

const logToPlugin = (message: string): void => {
  frontendLog("info", message).catch(() => undefined);
};

/** The overview type Steam hands back for a library entry, derived from the typed global. */
type AppOverview = NonNullable<ReturnType<Window["appStore"]["GetAppOverviewByAppID"]>>;

interface DragDropCollection {
  AddApps(apps: AppOverview[]): void;
  RemoveApps(apps: AppOverview[]): void;
}

export interface SteamCollection {
  id: string;
  displayName: string;
  bIsDeletable: boolean;
  apps: Map<number, AppOverview>;
  allApps: AppOverview[];
  AsDragDropCollection(): DragDropCollection;
  Save(): Promise<void>;
  Delete(): Promise<void>;
}

interface CollectionStore {
  userCollections: SteamCollection[];
  GetUserCollectionsByName?(name: string): SteamCollection[];
  NewUnsavedCollection(name: string, filterSpec: undefined, apps: AppOverview[]): SteamCollection;
}

declare global {
  interface Window {
    collectionStore?: CollectionStore;
  }
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const quote = (path: string): string => `"${path.replace(/"/g, '\\"')}"`;

/** Wait for Steam to populate the app overview for a freshly created shortcut. */
export async function waitForAppOverview(appId: number, attempts = 20): Promise<AppOverview | null> {
  for (let i = 0; i < attempts; i++) {
    const overview = window.appStore?.GetAppOverviewByAppID(appId);
    if (overview) {
      return overview;
    }
    await sleep(250);
  }
  return null;
}

export interface ShortcutSpec {
  name: string;
  exe: string;
  startDir: string;
  launchOptions: string;
}

/**
 * Create a non-Steam shortcut and return its app id. Paths are set again with
 * quotes after creation because Steam stores them quoted and some Steam
 * builds ignore the values passed to AddShortcut itself.
 */
export async function addShortcut(spec: ShortcutSpec): Promise<number> {
  const appId = await SteamClient.Apps.AddShortcut(spec.name, spec.exe, spec.startDir, spec.launchOptions);
  if (!appId) {
    throw new Error("Steam refused to create the shortcut");
  }
  await waitForAppOverview(appId);
  updateShortcut(appId, spec);
  return appId;
}

export function updateShortcut(appId: number, spec: Partial<ShortcutSpec>): void {
  if (spec.name !== undefined) {
    SteamClient.Apps.SetShortcutName(appId, spec.name);
  }
  if (spec.exe !== undefined) {
    SteamClient.Apps.SetShortcutExe(appId, quote(spec.exe));
  }
  if (spec.startDir !== undefined) {
    SteamClient.Apps.SetShortcutStartDir(appId, quote(spec.startDir));
  }
  if (spec.launchOptions !== undefined) {
    SteamClient.Apps.SetShortcutLaunchOptions(appId, spec.launchOptions);
  }
}

export function removeShortcut(appId: number): void {
  try {
    SteamClient.Apps.RemoveShortcut(appId);
  } catch (error) {
    console.warn("[FrostDeck] RemoveShortcut failed", appId, error);
  }
}

export function shortcutExists(appId: number | null | undefined): boolean {
  if (!appId) {
    return false;
  }
  return !!window.appStore?.GetAppOverviewByAppID(appId);
}

export interface CompatToolOption {
  name: string;
  displayName: string;
}

const looksLikeProton = (tool: { strToolName: string; strDisplayName: string }): boolean =>
  /proton/i.test(tool.strToolName) || /proton/i.test(tool.strDisplayName);

/**
 * Every Proton build on this Deck. Steam's client API is tried first (global
 * list, then per-app); if it yields nothing, the backend reads the builds from
 * disk, which is where Steam finds them anyway. Whatever the API returned is
 * written to the plugin log so on-device behaviour can be inspected.
 */
export async function listCompatTools(appId?: number): Promise<CompatToolOption[]> {
  let raw: unknown = null;
  let tools: Array<{ strToolName: string; strDisplayName: string }> = [];
  try {
    raw = await SteamClient.Settings.GetGlobalCompatTools();
    tools = Array.isArray(raw) ? raw : [];
  } catch (error) {
    logToPlugin(`GetGlobalCompatTools threw: ${String(error)}`);
  }
  logToPlugin(`GetGlobalCompatTools returned: ${JSON.stringify(raw)?.slice(0, 600)}; ${describeCompatApi()}`);
  if (tools.length === 0 && appId) {
    try {
      raw = await SteamClient.Apps.GetAvailableCompatTools(appId);
      tools = Array.isArray(raw) ? raw : [];
    } catch (error) {
      logToPlugin(`GetAvailableCompatTools threw: ${String(error)}`);
    }
    logToPlugin(`GetAvailableCompatTools(${appId}) returned: ${JSON.stringify(raw)?.slice(0, 600)}`);
  }
  const fromApi = tools
    .filter((tool) => tool && typeof tool.strToolName === "string")
    .filter(looksLikeProton)
    .map((tool) => ({ name: tool.strToolName, displayName: tool.strDisplayName || tool.strToolName }));
  if (fromApi.length > 0) {
    return fromApi;
  }
  try {
    const onDisk = await listCompatToolsOnDisk();
    logToPlugin(`Proton builds on disk: ${onDisk.map((tool) => tool.name).join(", ") || "none"}`);
    return onDisk.map((tool) => ({ name: tool.name, displayName: tool.display_name }));
  } catch (error) {
    logToPlugin(`list_compat_tools failed: ${String(error)}`);
    return [];
  }
}

/**
 * The compatibility tool Steam currently has on an app. Steam's app details
 * are asked first; if they are unavailable or report nothing, Steam's own
 * config.vdf is read. `name` is "" when nothing is set and Steam's default for
 * other titles applies.
 */
export async function getCompatToolFor(appId: number): Promise<CompatToolOption | null> {
  const fromDetails = await compatToolFromAppDetails(appId);
  if (fromDetails?.name) {
    return fromDetails;
  }
  try {
    const fromConfig = await getAppCompatToolFromConfig(appId);
    if (fromConfig) {
      return { name: fromConfig, displayName: fromConfig };
    }
  } catch (error) {
    logToPlugin(`get_app_compat_tool failed: ${String(error)}`);
  }
  return fromDetails ?? { name: "", displayName: "" };
}

async function compatToolFromAppDetails(appId: number): Promise<CompatToolOption | null> {
  const cached = window.appDetailsStore?.GetAppDetails(appId);
  if (cached) {
    return { name: cached.strCompatToolName ?? "", displayName: cached.strCompatToolDisplayName ?? "" };
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: CompatToolOption | null) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        registration?.unregister();
        resolve(value);
      }
    };
    const timer = setTimeout(() => finish(null), 2500);
    const registration = SteamClient.Apps.RegisterForAppDetails(appId, (details) =>
      finish({ name: details.strCompatToolName ?? "", displayName: details.strCompatToolDisplayName ?? "" }),
    );
  });
}

/**
 * Pick the best Proton build for Battle.net: a GE-Proton build if the user has
 * one (they handle Battle.net's launcher quirks best), otherwise Proton
 * Experimental, otherwise whatever Steam offers first.
 */
export function pickDefaultCompatTool(tools: CompatToolOption[]): string | null {
  const ge = tools
    .filter((tool) => /^GE-Proton/i.test(tool.name))
    .sort((a, b) => b.name.localeCompare(a.name, undefined, { numeric: true }));
  if (ge.length > 0) {
    return ge[0].name;
  }
  const experimental = tools.find((tool) => tool.name === "proton_experimental");
  if (experimental) {
    return experimental.name;
  }
  const anyProton = tools.find((tool) => /proton/i.test(tool.name));
  return anyProton?.name ?? tools[0]?.name ?? null;
}

/** Names of every compatibility-related function Steam exposes right now, for the log. */
export function describeCompatApi(): string {
  const pick = (obj: unknown) =>
    obj && typeof obj === "object" ? Object.keys(obj as object).filter((key) => /compat/i.test(key)).join(", ") : "n/a";
  return `Apps: [${pick(SteamClient.Apps)}] Settings: [${pick(SteamClient.Settings)}]`;
}

export function setCompatToolFor(appId: number, toolName: string): void {
  const apps = SteamClient.Apps as unknown as Record<string, unknown>;
  const setter = typeof apps.SpecifyCompatTool === "function" ? "SpecifyCompatTool" : null;
  if (!setter) {
    logToPlugin(`No compat tool setter found. ${describeCompatApi()}`);
    throw new Error("This Steam build offers no API to set the Proton version; set it in Steam → Properties → Compatibility.");
  }
  (apps[setter] as (appId: number, tool: string) => void)(appId, toolName);
  logToPlugin(`SpecifyCompatTool(${appId}, ${toolName})`);
}

/** Steam's ELibraryAssetType values for SetCustomArtworkForApp. */
const LIBRARY_ASSET_TYPE: Record<ArtworkKind, number> = { capsule: 0, hero: 1, logo: 2, header: 3 };

/** Apply fetched artwork to a shortcut. Returns how many assets took. */
export async function applyArtwork(appId: number, art: Artwork): Promise<number> {
  let applied = 0;
  for (const kind of Object.keys(LIBRARY_ASSET_TYPE) as ArtworkKind[]) {
    const asset = art.assets[kind];
    if (!asset) {
      continue;
    }
    try {
      await SteamClient.Apps.SetCustomArtworkForApp(appId, asset.b64, asset.ext, LIBRARY_ASSET_TYPE[kind]);
      applied++;
    } catch (error) {
      console.warn("[FrostDeck] SetCustomArtworkForApp failed", kind, error);
    }
  }
  if (art.icon_path) {
    try {
      SteamClient.Apps.SetShortcutIcon(appId, art.icon_path);
      applied++;
    } catch (error) {
      console.warn("[FrostDeck] SetShortcutIcon failed", error);
    }
  }
  return applied;
}

export function launchApp(appId: number): boolean {
  const overview = window.appStore?.GetAppOverviewByAppID(appId);
  if (!overview) {
    return false;
  }
  SteamClient.Apps.RunGame(overview.gameid, "", -1, 100);
  return true;
}

function findCollection(name: string): SteamCollection | undefined {
  const store = window.collectionStore;
  if (!store) {
    return undefined;
  }
  if (typeof store.GetUserCollectionsByName === "function") {
    const found = store.GetUserCollectionsByName(name);
    if (found && found.length > 0) {
      return found[0];
    }
  }
  return store.userCollections?.find((collection) => collection.displayName === name);
}

/**
 * Put an app into a named user collection, creating the collection if needed.
 * Returns false if the collection API is unavailable in this Steam build; the
 * shortcut still exists in the library, it just is not grouped.
 */
export async function addToCollection(name: string, appId: number): Promise<boolean> {
  const store = window.collectionStore;
  if (!store) {
    console.warn("[FrostDeck] collectionStore unavailable");
    return false;
  }
  const overview = await waitForAppOverview(appId);
  if (!overview) {
    return false;
  }
  try {
    let collection = findCollection(name);
    if (!collection) {
      collection = store.NewUnsavedCollection(name, undefined, [overview]);
      await collection.Save();
      return true;
    }
    if (collection.apps?.has(appId)) {
      return true;
    }
    collection.AsDragDropCollection().AddApps([overview]);
    await collection.Save();
    return true;
  } catch (error) {
    console.warn("[FrostDeck] addToCollection failed", error);
    return false;
  }
}

export async function removeFromCollection(name: string, appId: number): Promise<void> {
  const collection = findCollection(name);
  const overview = window.appStore?.GetAppOverviewByAppID(appId);
  if (!collection || !overview || !collection.apps?.has(appId)) {
    return;
  }
  try {
    collection.AsDragDropCollection().RemoveApps([overview]);
    await collection.Save();
  } catch (error) {
    console.warn("[FrostDeck] removeFromCollection failed", error);
  }
}
