import { callable } from "@decky/api";

export type BnetPhase = "none" | "installer" | "launcher";

export interface DetectedGame {
  config_key: string;
  launch_code: string;
  name: string;
  known: boolean;
  steam_app_id: number | null;
  install_path: string | null;
}

export interface ArtworkAsset {
  b64: string;
  ext: "jpg" | "png";
}

export type ArtworkKind = "capsule" | "header" | "hero" | "logo";

export interface Artwork {
  source: "sgdb" | "steam" | "none";
  assets: Partial<Record<ArtworkKind, ArtworkAsset>>;
  icon_path: string | null;
}

export interface PluginState {
  steam_root: string;
  shortcut_schema: number;
  current_shortcut_schema: number;
  installer_ready: boolean;
  installer_path: string;
  installer_launch_options: string;
  bnet_app_id: number | null;
  bnet_phase: BnetPhase;
  bnet_installed: boolean;
  bnet_exe: string | null;
  bnet_client_exe: string | null;
  bnet_start_dir: string | null;
  compatdata_dir: string | null;
  bnet_log_dir: string | null;
  launch_helper: string | null;
  helper_log: string;
  compat_tool: string | null;
  collection_name: string;
  games: DetectedGame[];
  game_app_ids: Record<string, number>;
  steam_default_compat_tool: string | null;
  wow: WowState | null;
  sgdb_api_key_set: boolean;
  sgdb_plugin_installed: boolean;
  artwork_applied: Record<string, boolean>;
}

export interface DownloadStatus {
  running: boolean;
  progress: number;
  total_bytes: number;
  error: string | null;
  ready: boolean;
  path: string;
}

export interface CatalogEntry {
  launch_code: string;
  name: string;
}

export const getState = callable<[], PluginState>("get_state");
export const downloadInstaller = callable<[], { started: boolean; ready: boolean; path: string }>(
  "download_installer",
);
export const getDownloadStatus = callable<[], DownloadStatus>("get_download_status");
export const setBnetShortcut = callable<[appId: number, phase: BnetPhase], void>("set_bnet_shortcut");
export const setShortcutSchema = callable<[version: number], void>("set_shortcut_schema");
export const setCompatTool = callable<[toolName: string | null], void>("set_compat_tool");
export const setGameAppId = callable<[launchCode: string, appId: number | null], void>("set_game_app_id");
export const resetBackend = callable<[deletePrefix: boolean], void>("reset");
export const fetchArtwork = callable<[name: string, launchCode: string, steamAppId: number | null], Artwork>("fetch_artwork");
export const setSgdbApiKey = callable<[key: string | null], void>("set_sgdb_api_key");
export const setArtworkApplied = callable<[code: string, applied: boolean], void>("set_artwork_applied");
export interface WowFlavor {
  dir: string;
  path: string;
  product: string;
  label: string;
  version: string;
  interface: number | null;
  addons_dir: string;
  installed: Record<string, { version: string; folders: string[]; installed_at: number }>;
}

export interface WowState {
  install_dir: string;
  flavors: WowFlavor[];
}

export interface AddonFlavorStatus {
  supported: boolean;
  latest_version: string | null;
  installed_version: string | null;
  update_available: boolean;
  fits: boolean | null;
}

export interface AddonRow {
  id: string;
  name: string;
  summary: string;
  category: string;
  featured: boolean;
  source: "github" | "tukui" | "wowi";
  latest_version: string | null;
  error: string | null;
  custom: boolean;
  repo: string | null;
  flavors: Record<string, AddonFlavorStatus>;
}

export interface UnmanagedAddon {
  group: string;
  folders: string[];
  title: string;
  version: string;
  adoptable_id: string | null;
}

export interface AddonListing {
  wow: WowState | null;
  addons: AddonRow[];
  unmanaged: Record<string, UnmanagedAddon[]>;
  game_running: boolean;
}

export interface AddonUpdateSummary {
  flavors: Record<string, { updates: number; names: string[] }>;
  game_running: boolean;
}

export const listAddons = callable<[refresh: boolean], AddonListing>("list_addons");
export const installAddon = callable<[addonId: string, flavorDir: string], { version: string; folders: string[]; fits: boolean | null }>("install_addon");
export const removeAddon = callable<[addonId: string, flavorDir: string], void>("remove_addon");
export const updateAddons = callable<[flavorDir: string], { updated: string[]; failed: Record<string, string> }>("update_addons");
export const adoptAddon = callable<[addonId: string, flavorDir: string], void>("adopt_addon");
export const addCustomAddon = callable<[repo: string], { id: string; name: string; version: string }>("add_custom_addon");
export const removeCustomAddon = callable<[addonId: string], void>("remove_custom_addon");
export const addonUpdateSummary = callable<[], AddonUpdateSummary>("addon_update_summary");

export interface AddonSearchResult {
  id: string;
  name: string;
  source: "github" | "tukui" | "wowi";
  summary: string;
  version: string | null;
  downloads: number | null;
  compatible: boolean | null;
  known: boolean;
  listed_for?: string[];
  updated?: number | null;
}
export interface AddonDetails {
  id: string;
  name: string;
  source: "github" | "tukui" | "wowi";
  author: string;
  version: string;
  updated: number | null;
  downloads: number | null;
  downloads_label?: string;
  description: string;
  changes: string;
  url: string;
  images: string[];
}
export const addonDetails = callable<[addonId: string], AddonDetails>("addon_details");
export const searchAddons = callable<[query: string, flavorDir: string | null], AddonSearchResult[]>("search_addons");
export const addWowiAddon = callable<[uid: string], { id: string; name: string }>("add_wowi_addon");

export interface CompatToolInfo {
  name: string;
  display_name: string;
  source: "steam" | "custom";
}
export const listCompatToolsOnDisk = callable<[], CompatToolInfo[]>("list_compat_tools");
export const getAppCompatToolFromConfig = callable<[appId: number], string | null>("get_app_compat_tool");
export const frontendLog = callable<[level: "debug" | "info" | "warning" | "error", message: string], void>("frontend_log");
export const gameInstallInfo = callable<[launchCode: string], { path: string | null; size_bytes: number; exists: boolean }>("game_install_info");
export const uninstallGame = callable<[launchCode: string], { removed: boolean; path: string }>("uninstall_game");
export const getCatalog = callable<[], CatalogEntry[]>("get_catalog");
