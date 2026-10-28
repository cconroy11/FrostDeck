import {
  ButtonItem,
  ConfirmModal,
  DialogButton,
  DropdownItem,
  Field,
  Focusable,
  Menu,
  MenuItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  ProgressBarWithInfo,
  TextField,
  showContextMenu,
  showModal,
  staticClasses,
} from "@decky/ui";
import { definePlugin, routerHook, toaster, useQuickAccessVisible } from "@decky/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { FaEllipsisH, FaSnowflake } from "react-icons/fa";

import { ADDONS_ROUTE, AddonsPage, requestAddonsFlavor } from "./AddonsPage";
import {
  addonUpdateSummary,
  gameInstallInfo,
  getState,
  setSgdbApiKey,
  uninstallGame,
  updateAddons,
  type AddonUpdateSummary,
  type DetectedGame,
  type PluginState,
  type WowFlavor,
} from "./backend";
import {
  addGameShortcut,
  applyArtworkFor,
  applyCompatToolEverywhere,
  fetchAllArtwork,
  installBattleNet,
  openBattleNet,
  playGame,
  promoteToLauncher,
  removeGameShortcut,
  repairShortcuts,
  resetEverything,
  shortcutsMissingArtwork,
  syncCompatToolFromSteam,
} from "./flows";
import { Section } from "./Section";
import { listCompatTools, shortcutExists, type CompatToolOption } from "./steam";

const errorText = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const shortVersion = (version: string): string => version.split(".").slice(0, 3).join(".");
const gigabytes = (bytes: number): string => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`);

function useBusy(): [boolean, (fn: () => Promise<void>) => Promise<void>] {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } finally {
      setBusy(false);
    }
  }, []);
  return [busy, run];
}

/** Proton builds Steam offers, loaded once Battle.net exists; shared by the sections that show them. */
function useCompatTools(state: PluginState): CompatToolOption[] {
  const [tools, setTools] = useState<CompatToolOption[]>([]);
  useEffect(() => {
    let cancelled = false;
    if (state.bnet_installed) {
      listCompatTools(state.bnet_app_id ?? undefined).then((list) => {
        if (!cancelled) {
          setTools(list);
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [state.bnet_installed, state.bnet_app_id]);
  return tools;
}

function protonLabel(state: PluginState, tools: CompatToolOption[]): string {
  const name = state.compat_tool ?? state.steam_default_compat_tool;
  if (!name) {
    return "Steam default Proton";
  }
  const display = tools.find((tool) => tool.name === name)?.displayName ?? name;
  return state.compat_tool ? display : `Steam default (${display})`;
}

// ---------------------------------------------------------------- Battle.net

function BattleNetSection(props: { state: PluginState; refresh: () => Promise<void> }) {
  const { state, refresh } = props;
  const [busy, run] = useBusy();
  const [progress, setProgress] = useState<number | null>(null);
  const tools = useCompatTools(state);

  const onInstall = () =>
    run(async () => {
      setProgress(0);
      try {
        await installBattleNet(state, (fraction) => setProgress(fraction));
      } catch (error) {
        toaster.toast({ title: "FrostDeck", body: errorText(error) });
      } finally {
        setProgress(null);
        await refresh();
      }
    });

  const onChangeTool = (toolName: string) => {
    const label = tools.find((tool) => tool.name === toolName)?.displayName ?? toolName;
    showModal(
      <ConfirmModal
        strTitle={`Switch to ${label}?`}
        strDescription="Applies to Battle.net and every game shortcut. Battle.net occasionally needs a repair after changing Proton builds; if it misbehaves, switch back."
        strOKButtonText="Switch"
        strCancelButtonText="Cancel"
        onOK={() =>
          run(async () => {
            try {
              await applyCompatToolEverywhere(state, toolName);
              toaster.toast({ title: "FrostDeck", body: `Proton set to ${label} on every shortcut.` });
            } catch (error) {
              toaster.toast({ title: "FrostDeck", body: errorText(error) });
            }
            await refresh();
          })
        }
      />,
    );
  };

  if (!state.bnet_installed) {
    const resumable = state.bnet_phase === "installer" && shortcutExists(state.bnet_app_id);
    return (
      <Section id="bnet" title="Battle.net" locked>
        {progress !== null && (
          <PanelSectionRow>
            <ProgressBarWithInfo
              nProgress={Math.round(progress * 100)}
              indeterminate={progress >= 1}
              sOperationText={progress >= 1 ? "Setting up Steam shortcut…" : "Downloading installer…"}
              sTimeRemaining={progress < 1 ? `${Math.round(progress * 100)}%` : undefined}
              focusable={false}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
            onClick={resumable ? () => openBattleNet(state) : onInstall}
            description={
              resumable
                ? "The installer shortcut already exists. Finish installing and sign in, then come back here."
                : "Downloads the official installer, adds a “Battle.net” shortcut under Proton in a Blizzard collection, and opens it."
            }
          >
            {resumable ? "Resume Battle.net installer" : "Install Battle.net"}
          </ButtonItem>
        </PanelSectionRow>
        {resumable && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={onInstall}>
              Re-run installer setup
            </ButtonItem>
          </PanelSectionRow>
        )}
      </Section>
    );
  }

  return (
    <Section id="bnet" title="Battle.net" summary={`Ready · ${protonLabel(state, tools)}`} defaultOpen={false}>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => openBattleNet(state)}>
          Open Battle.net
        </ButtonItem>
      </PanelSectionRow>
      {tools.length > 0 ? (
        <PanelSectionRow>
          <DropdownItem
            layout="below"
            label="Proton version"
            description="Shared by Battle.net and every game shortcut. A build chosen for one game in Steam's Properties is kept."
            rgOptions={tools.map((tool) => ({ data: tool.name, label: tool.displayName || tool.name }))}
            selectedOption={state.compat_tool}
            strDefaultLabel={protonLabel(state, tools)}
            disabled={busy}
            onChange={(option) => {
              if (option.data !== state.compat_tool) {
                onChangeTool(option.data as string);
              }
            }}
          />
        </PanelSectionRow>
      ) : (
        <PanelSectionRow>
          <Field label="Proton version" description="No Proton builds found. Install one in Steam → Settings → Compatibility, then reopen this panel." focusable={false} />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={busy}
          description="Rewrites the Battle.net and game shortcuts with the current executable, launch options and Proton build."
          onClick={() =>
            run(async () => {
              const count = await repairShortcuts(state);
              toaster.toast({ title: "FrostDeck", body: `Repaired ${count} shortcut(s).` });
              await refresh();
            })
          }
        >
          Repair shortcuts
        </ButtonItem>
      </PanelSectionRow>
    </Section>
  );
}

// ---------------------------------------------------------------- Games

function GameRow(props: { state: PluginState; game: DetectedGame; refresh: () => Promise<void> }) {
  const { state, game, refresh } = props;
  const [busy, run] = useBusy();
  const appId = state.game_app_ids[game.launch_code];
  const inSteam = shortcutExists(appId);

  const onAdd = () =>
    run(async () => {
      try {
        await addGameShortcut(state, game);
        toaster.toast({ title: game.name, body: `Added to Steam under “${state.collection_name}”.` });
      } catch (error) {
        toaster.toast({ title: game.name, body: errorText(error) });
      }
      await refresh();
    });

  const onPlay = () => {
    if (!playGame(state, game.launch_code)) {
      toaster.toast({ title: game.name, body: "Shortcut missing. Add it to Steam again." });
    }
  };

  const onUninstall = () =>
    run(async () => {
      const info = await gameInstallInfo(game.launch_code);
      if (!info.exists || !info.path) {
        toaster.toast({ title: game.name, body: "Install folder not found. Uninstall it from inside Battle.net." });
        return;
      }
      const shortPath = info.path.replace(/^.*\/drive_c\//, "C:\\").replace(/\//g, "\\");
      showModal(
        <ConfirmModal
          strTitle={`Uninstall ${game.name}?`}
          strDescription={`Deletes ${gigabytes(info.size_bytes)} at ${shortPath} and removes the Steam shortcut. Battle.net will offer to install it again; your settings and saves in Battle.net are kept.`}
          strOKButtonText="Uninstall"
          strCancelButtonText="Cancel"
          bDestructiveWarning
          onOK={() =>
            run(async () => {
              try {
                await removeGameShortcut(state, game.launch_code);
                const result = await uninstallGame(game.launch_code);
                toaster.toast({ title: game.name, body: result.removed ? `Uninstalled, ${gigabytes(info.size_bytes)} freed.` : "Nothing to remove." });
              } catch (error) {
                toaster.toast({ title: game.name, body: errorText(error) });
              }
              await refresh();
            })
          }
        />,
      );
    });

  const onMore = (event: MouseEvent) =>
    showContextMenu(
      <Menu label={game.name}>
        <MenuItem
          onSelected={() =>
            run(async () => {
              const count = await applyArtworkFor(state, game.launch_code, game.name, appId, game.steam_app_id);
              toaster.toast({ title: game.name, body: count > 0 ? "Artwork refreshed." : "No artwork found." });
              await refresh();
            })
          }
        >
          Fetch artwork again
        </MenuItem>
        <MenuItem onSelected={() => openBattleNet(state)}>Open in Battle.net</MenuItem>
        <MenuItem
          onSelected={() =>
            run(async () => {
              await removeGameShortcut(state, game.launch_code);
              toaster.toast({ title: game.name, body: "Shortcut removed from Steam. The game stays installed; add it again any time." });
              await refresh();
            })
          }
        >
          Remove from Steam only
        </MenuItem>
        <MenuItem tone="destructive" onSelected={onUninstall}>
          Uninstall from Deck…
        </MenuItem>
      </Menu>,
      event.currentTarget ?? undefined,
    );

  return (
    <PanelSectionRow>
      <Field
        label={game.name}
        description={inSteam ? "In Steam" : game.known ? "Installed in Battle.net, not in Steam yet" : `Unknown product “${game.config_key}”`}
        childrenLayout="below"
        bottomSeparator="standard"
      >
        <Focusable style={{ display: "flex", gap: "8px" }}>
          {inSteam ? (
            <>
              <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy} onClick={onPlay}>
                Play
              </DialogButton>
              <DialogButton style={{ flex: "0 0 48px", minWidth: 0, padding: "10px 0" }} disabled={busy} onClick={onMore}>
                <FaEllipsisH />
              </DialogButton>
            </>
          ) : (
            <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy} onClick={onAdd}>
              Add to Steam
            </DialogButton>
          )}
        </Focusable>
      </Field>
    </PanelSectionRow>
  );
}

function GamesSection(props: { state: PluginState; refresh: () => Promise<void> }) {
  const { state, refresh } = props;
  const [busy, run] = useBusy();
  if (!state.bnet_installed) {
    return null;
  }
  const missing = state.games.filter((game) => !shortcutExists(state.game_app_ids[game.launch_code]));
  const summary =
    state.games.length === 0
      ? "No games installed yet"
      : `${state.games.length} game${state.games.length === 1 ? "" : "s"}${missing.length ? ` · ${missing.length} not in Steam` : ""}`;

  const onAddAll = () =>
    run(async () => {
      for (const game of missing) {
        try {
          await addGameShortcut(state, game);
        } catch (error) {
          toaster.toast({ title: game.name, body: errorText(error) });
        }
      }
      toaster.toast({ title: "FrostDeck", body: `Added ${missing.length} game(s) to Steam.` });
      await refresh();
    });

  return (
    <Section id="games" title="Games" summary={summary}>
      {state.games.length === 0 ? (
        <PanelSectionRow>
          <Field description="Install a game inside Battle.net and it appears here, ready to add to Steam." focusable={false} />
        </PanelSectionRow>
      ) : (
        state.games.map((game) => <GameRow key={game.launch_code} state={state} game={game} refresh={refresh} />)
      )}
      <PanelSectionRow>
        <Focusable style={{ display: "flex", gap: "8px", paddingTop: "12px", marginTop: "4px", borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          {missing.length > 1 && (
            <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy} onClick={onAddAll}>
              Add all to Steam
            </DialogButton>
          )}
          <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy} onClick={() => refresh()}>
            Rescan
          </DialogButton>
        </Focusable>
      </PanelSectionRow>
    </Section>
  );
}

// ---------------------------------------------------------------- World of Warcraft

function FlavorRow(props: {
  flavor: WowFlavor;
  updates: { updates: number; names: string[] } | undefined;
  gameRunning: boolean;
  onUpdated: () => Promise<void>;
}) {
  const { flavor, updates, gameRunning, onUpdated } = props;
  const [busy, run] = useBusy();
  const addonCount = Object.keys(flavor.installed).length;
  const pending = updates?.updates ?? 0;

  const openAddons = () => {
    requestAddonsFlavor(flavor.dir);
    Navigation.Navigate(ADDONS_ROUTE);
    Navigation.CloseSideMenus();
  };

  const onUpdateAll = () =>
    run(async () => {
      try {
        const result = await updateAddons(flavor.dir);
        const failed = Object.keys(result.failed).length;
        toaster.toast({ title: flavor.label, body: `${result.updated.length} addon(s) updated${failed ? `, ${failed} failed` : ""}.` });
      } catch (error) {
        toaster.toast({ title: flavor.label, body: errorText(error) });
      }
      await onUpdated();
    });

  let description: string;
  if (addonCount === 0) {
    description = "No addons installed";
  } else if (pending > 0) {
    description = `${addonCount} addon${addonCount === 1 ? "" : "s"} · ${pending} update${pending === 1 ? "" : "s"} available${gameRunning ? " (close WoW first)" : ""}`;
  } else if (updates) {
    description = `${addonCount} addon${addonCount === 1 ? "" : "s"} · up to date`;
  } else {
    description = `${addonCount} addon${addonCount === 1 ? "" : "s"} installed`;
  }

  return (
    <PanelSectionRow>
      <Field label={`${flavor.label} ${shortVersion(flavor.version)}`} description={description} childrenLayout="below" bottomSeparator="standard">
        <Focusable style={{ display: "flex", gap: "8px" }}>
          {pending > 0 && (
            <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy || gameRunning} onClick={onUpdateAll}>
              Update all
            </DialogButton>
          )}
          <DialogButton style={{ flex: 1, minWidth: 0 }} disabled={busy} onClick={openAddons}>
            Addons
          </DialogButton>
        </Focusable>
      </Field>
    </PanelSectionRow>
  );
}

function WowSection(props: { state: PluginState; refresh: () => Promise<void> }) {
  const { state, refresh } = props;
  const wow = state.wow;
  const [summary, setSummary] = useState<AddonUpdateSummary | null>(null);

  const checkUpdates = useCallback(async () => {
    try {
      setSummary(await addonUpdateSummary());
    } catch (error) {
      console.warn("[FrostDeck] addon update check failed", error);
    }
  }, []);

  useEffect(() => {
    if (wow && wow.flavors.length > 0) {
      void checkUpdates();
    }
  }, [wow?.install_dir, checkUpdates]);

  if (!wow || wow.flavors.length === 0) {
    return null;
  }
  const totalPending = Object.values(summary?.flavors ?? {}).reduce((sum, entry) => sum + entry.updates, 0);
  const headline = wow.flavors
    .map((flavor) => `${flavor.label} · ${Object.keys(flavor.installed).length} addon${Object.keys(flavor.installed).length === 1 ? "" : "s"}`)
    .join(" / ");
  return (
    <Section id="wow" title="World of Warcraft" summary={totalPending > 0 ? `${headline} · ${totalPending} update${totalPending === 1 ? "" : "s"}` : headline}>
      {wow.flavors.map((flavor) => (
        <FlavorRow
          key={flavor.dir}
          flavor={flavor}
          updates={summary?.flavors[flavor.dir]}
          gameRunning={!!summary?.game_running}
          onUpdated={async () => {
            await refresh();
            await checkUpdates();
          }}
        />
      ))}
    </Section>
  );
}

// ---------------------------------------------------------------- Settings

function SettingsSection(props: { state: PluginState; refresh: () => Promise<void> }) {
  const { state, refresh } = props;
  const [busy, run] = useBusy();
  const [keyDraft, setKeyDraft] = useState("");

  const onSaveKey = async () => {
    const trimmed = keyDraft.trim();
    await setSgdbApiKey(trimmed || null);
    setKeyDraft("");
    toaster.toast({ title: "FrostDeck", body: trimmed ? "SteamGridDB key saved." : "SteamGridDB key cleared." });
    await refresh();
  };

  const onFetchArt = () =>
    run(async () => {
      const count = await fetchAllArtwork(state, true);
      toaster.toast({ title: "FrostDeck", body: `Artwork applied to ${count} shortcut(s).` });
      await refresh();
    });

  const onReset = () => {
    showModal(
      <ConfirmModal
        strTitle="Reset FrostDeck?"
        strDescription="Removes the Battle.net and game shortcuts this plugin created. Choose whether to also delete the Proton prefix (Battle.net itself and every installed game)."
        strOKButtonText="Remove shortcuts only"
        strMiddleButtonText="Delete everything"
        strCancelButtonText="Cancel"
        bDestructiveWarning
        onOK={() =>
          run(async () => {
            await resetEverything(false);
            await refresh();
          })
        }
        onMiddleButton={() =>
          run(async () => {
            await resetEverything(true);
            await refresh();
          })
        }
      />,
    );
  };

  const [editingKey, setEditingKey] = useState(false);
  const showKeyField = !state.sgdb_api_key_set || editingKey;
  const keyHelp = state.sgdb_plugin_installed
    ? "Optional. You already have the SteamGridDB plugin for picking artwork by hand; a key lets FrostDeck apply artwork automatically when games are added. Free at steamgriddb.com → Profile → Preferences → API."
    : "Optional. Lets FrostDeck apply library artwork automatically when games are added. Free at steamgriddb.com → Profile → Preferences → API.";

  return (
    <Section id="settings" title="Settings" summary={state.sgdb_api_key_set ? "Artwork automatic" : "Artwork manual"} defaultOpen={false}>
      {showKeyField ? (
        <PanelSectionRow>
          <Field label="SteamGridDB API key" description={keyHelp} childrenLayout="below">
            <TextField value={keyDraft} bIsPassword onChange={(event) => setKeyDraft(event.target.value)} />
          </Field>
        </PanelSectionRow>
      ) : (
        <PanelSectionRow>
          <Field label="Artwork" description="Automatic, using your SteamGridDB key." childrenContainerWidth="min">
            <DialogButton style={{ minWidth: "110px", padding: "10px 14px" }} disabled={busy} onClick={() => setEditingKey(true)}>
              Change key
            </DialogButton>
          </Field>
        </PanelSectionRow>
      )}
      {showKeyField && keyDraft.trim().length > 0 && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await onSaveKey();
                setEditingKey(false);
              })
            }
          >
            Save key
          </ButtonItem>
        </PanelSectionRow>
      )}
      {state.bnet_installed && (state.sgdb_api_key_set || state.games.some((game) => game.steam_app_id)) && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy}
            description="Re-fetches capsule, hero, logo, header and icon for Battle.net and every game shortcut."
            onClick={onFetchArt}
          >
            Fetch artwork for all
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} description="Removes what the plugin created. Asks before touching anything." onClick={onReset}>
          Reset FrostDeck
        </ButtonItem>
      </PanelSectionRow>
    </Section>
  );
}

// ---------------------------------------------------------------- Panel

function Content() {
  const [state, setState] = useState<PluginState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visible = useQuickAccessVisible();
  const promoting = useRef(false);
  const artworkRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      let next = await getState();
      // Battle.net finished installing since we last looked: swap the shortcut
      // from installer to launcher exactly once.
      if (next.bnet_installed && next.bnet_phase === "installer" && !promoting.current) {
        promoting.current = true;
        try {
          if (await promoteToLauncher(next)) {
            next = await getState();
          }
        } finally {
          promoting.current = false;
        }
      }
      // Shortcut format changed in an update: rewrite existing shortcuts once.
      if (next.bnet_installed && next.shortcut_schema < next.current_shortcut_schema && !promoting.current) {
        promoting.current = true;
        try {
          const count = await repairShortcuts(next);
          if (count > 0) {
            toaster.toast({ title: "FrostDeck", body: `Updated ${count} shortcut(s) to the new launch format.` });
          }
          next = await getState();
        } finally {
          promoting.current = false;
        }
      }
      if (await syncCompatToolFromSteam(next)) {
        next = await getState();
      }
      setState(next);
      setError(null);
      // Shortcuts without art yet (e.g. key just added, or an older install): fill them in without blocking the UI.
      if (!artworkRunning.current && shortcutsMissingArtwork(next).length > 0 && (next.sgdb_api_key_set || next.games.some((g) => g.steam_app_id))) {
        artworkRunning.current = true;
        fetchAllArtwork(next)
          .then(async (count) => {
            if (count > 0) {
              toaster.toast({ title: "FrostDeck", body: `Artwork applied to ${count} shortcut(s).` });
              setState(await getState());
            }
          })
          .finally(() => {
            artworkRunning.current = false;
          });
      }
    } catch (caught) {
      setError(errorText(caught));
    }
  }, []);

  useEffect(() => {
    if (visible) {
      void refresh();
    }
  }, [visible, refresh]);

  if (error) {
    return (
      <PanelSection title="FrostDeck">
        <PanelSectionRow>
          <Field description={`Backend error: ${error}`} focusable={false} />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => refresh()}>
            Retry
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (!state) {
    return <PanelSection title="FrostDeck" spinner />;
  }

  return (
    <>
      <BattleNetSection state={state} refresh={refresh} />
      <GamesSection state={state} refresh={refresh} />
      <WowSection state={state} refresh={refresh} />
      <SettingsSection state={state} refresh={refresh} />
    </>
  );
}

export default definePlugin(() => {
  routerHook.addRoute(ADDONS_ROUTE, AddonsPage, { exact: true });
  return {
    name: "FrostDeck",
    titleView: <div className={staticClasses.Title}>FrostDeck</div>,
    content: <Content />,
    icon: <FaSnowflake />,
    onDismount() {
      routerHook.removeRoute(ADDONS_ROUTE);
    },
  };
});
