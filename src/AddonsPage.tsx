import { ConfirmModal, DialogButton, Dropdown, Focusable, Menu, MenuItem, Navigation, SteamSpinner, Tabs, TextField, showContextMenu, showModal } from "@decky/ui";
import { toaster } from "@decky/api";
import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { FaEllipsisH, FaSyncAlt } from "react-icons/fa";

import {
  addCustomAddon,
  addWowiAddon,
  addonDetails,
  adoptAddon,
  installAddon,
  listAddons,
  removeAddon,
  removeCustomAddon,
  searchAddons,
  updateAddons,
  type AddonDetails,
  type AddonListing,
  type AddonRow,
  type AddonSearchResult,
  type UnmanagedAddon,
  type WowFlavor,
} from "./backend";

export const ADDONS_ROUTE = "/frostdeck/addons";

/** Flavour the panel asked the page to open on; consumed on the page's first load. */
let requestedFlavorDir: string | null = null;
export function requestAddonsFlavor(dir: string | null): void {
  requestedFlavorDir = dir;
}

const errorText = (error: unknown): string => (error instanceof Error ? error.message : String(error));
const SOURCE_LABEL: Record<string, string> = { github: "GitHub", tukui: "Tukui", wowi: "WoWInterface" };
const compact = (n: number): string => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
const shortVersion = (version: string): string => version.split(".").slice(0, 3).join(".");
const dateText = (epoch: number | null | undefined): string => (epoch ? new Date(epoch * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "");

type SortKey = "downloads" | "updated" | "name";
type SourceFilter = "all" | "github" | "wowi";
const SORT_OPTIONS: Array<{ data: SortKey; label: string }> = [
  { data: "downloads", label: "Most downloaded" },
  { data: "updated", label: "Recently updated" },
  { data: "name", label: "Name A–Z" },
];
const SOURCE_OPTIONS: Array<{ data: SourceFilter; label: string }> = [
  { data: "all", label: "All sources" },
  { data: "github", label: "GitHub" },
  { data: "wowi", label: "WoWInterface" },
];

/** Prefer Retail when several flavours are installed. */
export function defaultFlavor(flavors: WowFlavor[]): WowFlavor | undefined {
  return flavors.find((flavor) => flavor.dir === "_retail_") ?? flavors[0];
}

// ---------------------------------------------------------------- building blocks

const rowStyle = {
  display: "flex",
  alignItems: "center",
  gap: "14px",
  padding: "10px 14px",
  margin: "4px 0",
  borderRadius: "6px",
  background: "rgba(255, 255, 255, 0.04)",
} as const;

const oneLine = { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } as const;

/**
 * One addon row: title and a single subtitle line on the left, which can be
 * pressed to open the details sheet, and one or two actions on the right.
 */
function Row(props: { title: string; subtitle: string; highlight?: boolean; onOpen?: () => void; children?: ReactNode }) {
  const { title, subtitle, highlight, onOpen, children } = props;
  return (
    <Focusable style={{ ...rowStyle, background: highlight ? "rgba(79, 141, 255, 0.14)" : rowStyle.background }}>
      <Focusable
        style={{ flex: 1, minWidth: 0, padding: "4px 6px", margin: "-4px -6px", borderRadius: "4px" }}
        focusClassName="gpfocus"
        onActivate={onOpen}
        onClick={onOpen}
      >
        <div style={{ fontSize: "15px", fontWeight: 600, ...oneLine }}>{title}</div>
        <div style={{ fontSize: "12px", opacity: 0.65, marginTop: "2px", ...oneLine }}>{subtitle}</div>
      </Focusable>
      <Focusable style={{ display: "flex", gap: "8px", flexShrink: 0 }}>{children}</Focusable>
    </Focusable>
  );
}

/** Steam's dialog buttons stretch to fill flex rows; these keep a fixed footprint. */
function Button(props: { label: ReactNode; onClick: (event: MouseEvent) => void; disabled?: boolean; narrow?: boolean }) {
  const { label, onClick, disabled, narrow } = props;
  return (
    <DialogButton
      style={{ width: narrow ? "44px" : "auto", minWidth: narrow ? "44px" : "100px", maxWidth: "200px", flex: "0 0 auto", padding: narrow ? "10px 0" : "10px 16px" }}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
    </DialogButton>
  );
}

function Heading(props: { children: ReactNode; sub?: string }) {
  return (
    <div style={{ margin: "18px 0 6px" }}>
      <div style={{ fontSize: "16px", fontWeight: 700 }}>{props.children}</div>
      {props.sub && <div style={{ fontSize: "12px", opacity: 0.6 }}>{props.sub}</div>}
    </div>
  );
}

function Muted(props: { children: ReactNode }) {
  return <span style={{ fontSize: "12px", opacity: 0.5, alignSelf: "center", whiteSpace: "nowrap" }}>{props.children}</span>;
}

// ---------------------------------------------------------------- page

export function AddonsPage() {
  const [listing, setListing] = useState<AddonListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flavorDir, setFlavorDir] = useState<string | null>(null);
  const [tab, setTab] = useState<"installed" | "browse">("installed");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AddonSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [showRepoField, setShowRepoField] = useState(false);
  const [repoDraft, setRepoDraft] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("downloads");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");

  const load = useCallback(async (refresh = false) => {
    try {
      const next = await listAddons(refresh);
      setListing(next);
      setError(null);
      setFlavorDir((current) => {
        const flavors = next.wow?.flavors ?? [];
        if (requestedFlavorDir && flavors.some((flavor) => flavor.dir === requestedFlavorDir)) {
          const chosen = requestedFlavorDir;
          requestedFlavorDir = null;
          return chosen;
        }
        if (current && flavors.some((flavor) => flavor.dir === current)) {
          return current;
        }
        return defaultFlavor(flavors)?.dir ?? null;
      });
    } catch (caught) {
      setError(errorText(caught));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const flavor = useMemo(() => listing?.wow?.flavors.find((entry) => entry.dir === flavorDir) ?? null, [listing, flavorDir]);
  const gameRunning = !!listing?.game_running;
  const locked = busyId !== null || gameRunning;

  // Search as the user types, a short pause after the last keystroke.
  useEffect(() => {
    const term = query.trim();
    if (term.length < 2) {
      setResults(null);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(async () => {
      try {
        const found = await searchAddons(term, flavorDir);
        if (!cancelled) {
          setResults(found);
        }
      } catch (caught) {
        if (!cancelled) {
          toaster.toast({ title: "Search", body: errorText(caught) });
        }
      } finally {
        if (!cancelled) {
          setSearching(false);
        }
      }
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, flavorDir]);

  const run = async (id: string, work: () => Promise<void>) => {
    setBusyId(id);
    try {
      await work();
    } finally {
      setBusyId(null);
    }
  };

  const withToast = (title: string, work: () => Promise<string>) => async () => {
    try {
      toaster.toast({ title, body: await work() });
    } catch (caught) {
      toaster.toast({ title, body: errorText(caught) });
    }
    await load();
  };

  const installById = async (id: string): Promise<string> => {
    if (!flavor) {
      throw new Error("No flavour selected");
    }
    const result = await installAddon(id, flavor.dir);
    const note = result.fits === null ? " It doesn't declare supported versions; check it loads in the AddOns menu." : "";
    return `Installed ${result.version} for ${flavor.label}.${note}`;
  };

  const onInstall = (id: string, name: string) => run(id, withToast(name, () => installById(id)));
  const onInstallResult = (result: AddonSearchResult) =>
    run(
      result.id,
      withToast(result.name, async () => {
        if (result.source === "wowi" && !result.known) {
          await addWowiAddon(result.id.split(":")[1]);
        }
        return installById(result.id);
      }),
    );
  const onRemove = (addon: AddonRow) =>
    run(
      addon.id,
      withToast(addon.name, async () => {
        if (!flavor) {
          throw new Error("No flavour selected");
        }
        await removeAddon(addon.id, flavor.dir);
        return `Removed from ${flavor.label}.`;
      }),
    );
  const onForget = (addon: AddonRow) =>
    run(
      addon.id,
      withToast(addon.name, async () => {
        await removeCustomAddon(addon.id);
        return "Removed from Browse.";
      }),
    );
  const onAdopt = (item: UnmanagedAddon) =>
    run(
      `adopt:${item.group}`,
      withToast(item.title, async () => {
        if (!flavor || !item.adoptable_id) {
          throw new Error("Nothing to adopt");
        }
        await adoptAddon(item.adoptable_id, flavor.dir);
        return "Now managed by FrostDeck; updates will be offered here.";
      }),
    );
  const onAddRepo = () =>
    run(
      "__repo__",
      withToast("GitHub addon", async () => {
        const added = await addCustomAddon(repoDraft);
        setRepoDraft("");
        setShowRepoField(false);
        return `Added ${added.name} (${added.version}). It's now listed under Browse.`;
      }),
    );
  const onUpdateAll = () =>
    run(
      "__all__",
      withToast("Addons", async () => {
        if (!flavor) {
          throw new Error("No flavour selected");
        }
        const result = await updateAddons(flavor.dir);
        const failed = Object.keys(result.failed).length;
        return `${result.updated.length} updated${failed ? `, ${failed} failed` : ""}.`;
      }),
    );

  const openDetails = (id: string, name: string, installable: boolean) => async () => {
    let details: AddonDetails;
    try {
      details = await addonDetails(id);
    } catch (caught) {
      toaster.toast({ title: name, body: errorText(caught) });
      return;
    }
    const facts = [
      details.author ? `By ${details.author}` : "",
      SOURCE_LABEL[details.source] ?? details.source,
      details.version ? `Version ${details.version}` : "",
      details.updated ? `Updated ${dateText(details.updated)}` : "",
      details.downloads ? `${compact(details.downloads)} ${details.downloads_label ?? "downloads"}` : "",
    ].filter(Boolean);
    const body = (
      <div style={{ fontSize: "13px", lineHeight: 1.45 }}>
        <div style={{ opacity: 0.7, marginBottom: "10px" }}>{facts.join(" · ")}</div>
        {details.images[0] && (
          <img
            src={details.images[0]}
            alt=""
            style={{ display: "block", width: "100%", maxHeight: "220px", objectFit: "contain", borderRadius: "6px", background: "rgba(0,0,0,0.3)", marginBottom: "12px" }}
          />
        )}
        {details.description && <div style={{ whiteSpace: "pre-wrap", marginBottom: details.changes ? "12px" : 0 }}>{details.description}</div>}
        {details.changes && (
          <>
            <div style={{ fontWeight: 600, marginBottom: "4px" }}>Recent changes</div>
            <div style={{ whiteSpace: "pre-wrap", opacity: 0.85, maxHeight: "180px", overflow: "hidden" }}>{details.changes}</div>
          </>
        )}
      </div>
    );
    showModal(
      <ConfirmModal
        strTitle={details.name}
        strDescription={body}
        strOKButtonText={installable ? "Install" : "Close"}
        strMiddleButtonText={details.url ? "View page" : undefined}
        strCancelButtonText={installable ? "Close" : undefined}
        bAlertDialog={!installable && !details.url}
        onOK={() => {
          if (installable) {
            void onInstall(id, name);
          }
        }}
        onMiddleButton={() => {
          if (details.url) {
            Navigation.NavigateToExternalWeb(details.url);
          }
        }}
      />,
    );
  };

  const openMenu = (addon: AddonRow, status: AddonRow["flavors"][string] | undefined) => (event: MouseEvent) =>
    showContextMenu(
      <Menu label={addon.name}>
        {status?.supported && <MenuItem onSelected={() => onInstall(addon.id, addon.name)}>Reinstall</MenuItem>}
        {addon.custom && !Object.values(addon.flavors).some((entry) => entry.installed_version) && (
          <MenuItem onSelected={() => onForget(addon)}>Remove from Browse</MenuItem>
        )}
        <MenuItem tone="destructive" onSelected={() => onRemove(addon)}>
          Remove from {flavor?.label ?? "this flavour"}
        </MenuItem>
      </Menu>,
      event.currentTarget ?? undefined,
    );

  // ------------------------------------------------------------ derived lists

  const addons = listing?.addons ?? [];
  const statusOf = (addon: AddonRow) => (flavor ? addon.flavors[flavor.dir] : undefined);
  const installed = addons.filter((addon) => statusOf(addon)?.installed_version);
  const updates = installed.filter((addon) => statusOf(addon)?.update_available);
  const current = installed.filter((addon) => !statusOf(addon)?.update_available);
  const unmanaged = flavor ? (listing?.unmanaged[flavor.dir] ?? []) : [];
  const recommended = addons.filter((addon) => addon.featured);
  const catalog = addons.filter((addon) => !addon.featured).sort((a, b) => a.name.localeCompare(b.name));
  const isInstalled = (id: string) => installed.some((addon) => addon.id === id);
  const totalInstalled = installed.length + unmanaged.length;

  const subtitleFor = (addon: AddonRow): string => {
    const status = statusOf(addon);
    const source = SOURCE_LABEL[addon.source] ?? addon.source;
    if (status?.update_available) {
      return `Update available · ${status.installed_version} → ${status.latest_version}`;
    }
    if (status?.installed_version) {
      return `${status.installed_version} · up to date · ${source}`;
    }
    return addon.summary;
  };

  // ------------------------------------------------------------ views

  const installedView = (
    <div>
      <Focusable style={{ display: "flex", alignItems: "center", gap: "12px", margin: "12px 0 6px" }}>
        <div style={{ flex: 1, fontSize: "13px", opacity: 0.7 }}>
          {totalInstalled === 0
            ? "Nothing installed yet."
            : updates.length > 0
              ? `${totalInstalled} installed · ${updates.length} update${updates.length === 1 ? "" : "s"} available`
              : `${totalInstalled} installed · all up to date`}
        </div>
        {updates.length > 0 && <Button label={`Update all (${updates.length})`} disabled={locked} onClick={onUpdateAll} />}
        <Button narrow label={<FaSyncAlt />} disabled={busyId !== null} onClick={() => load(true)} />
      </Focusable>

      {totalInstalled === 0 && (
        <Focusable style={{ ...rowStyle, justifyContent: "space-between" }}>
          <div style={{ fontSize: "13px", opacity: 0.8 }}>Start with ConsolePort for controller support, then add what you use.</div>
          <Button label="Browse" onClick={() => setTab("browse")} />
        </Focusable>
      )}

      {[...updates, ...current].map((addon) => {
        const status = statusOf(addon);
        return (
          <Row key={addon.id} title={addon.name} subtitle={subtitleFor(addon)} highlight={!!status?.update_available} onOpen={openDetails(addon.id, addon.name, false)}>
            {status?.update_available && <Button label="Update" disabled={locked} onClick={() => onInstall(addon.id, addon.name)} />}
            <Button narrow label={<FaEllipsisH />} disabled={locked} onClick={openMenu(addon, status)} />
          </Row>
        );
      })}

      {unmanaged.length > 0 && (
        <>
          <Heading sub="In the AddOns folder, but not installed through FrostDeck.">Found in {flavor?.label}</Heading>
          {unmanaged.map((item) => (
            <Row
              key={item.group}
              title={item.title}
              subtitle={`${item.version ? `${item.version} · ` : ""}${item.folders.length === 1 ? item.folders[0] : `${item.folders.length} folders`}`}
            >
              {item.adoptable_id ? <Button label="Manage" disabled={locked} onClick={() => onAdopt(item)} /> : <Muted>Not managed</Muted>}
            </Row>
          ))}
        </>
      )}
    </div>
  );

  const resultRow = (result: AddonSearchResult) => {
    const parts = [SOURCE_LABEL[result.source] ?? result.source];
    if (result.version) {
      parts.push(result.version);
    }
    if (result.downloads) {
      parts.push(`${compact(result.downloads)} downloads`);
    }
    if (result.updated) {
      parts.push(dateText(result.updated));
    }
    const already = isInstalled(result.id);
    return (
      <Row key={result.id} title={result.name} subtitle={parts.join(" · ")} onOpen={openDetails(result.id, result.name, !already && !locked)}>
        <Button label={already ? "Installed" : "Install"} disabled={locked || already} onClick={() => onInstallResult(result)} />
      </Row>
    );
  };

  const visibleResults = useMemo(() => {
    if (!results) {
      return null;
    }
    const filtered = results.filter((result) => sourceFilter === "all" || result.source === sourceFilter || (sourceFilter === "github" && result.source === "tukui"));
    const known = filtered.filter((result) => result.known);
    const rest = filtered.filter((result) => !result.known);
    const by: Record<SortKey, (a: AddonSearchResult, b: AddonSearchResult) => number> = {
      downloads: (a, b) => (b.downloads ?? 0) - (a.downloads ?? 0),
      updated: (a, b) => (b.updated ?? 0) - (a.updated ?? 0),
      name: (a, b) => a.name.localeCompare(b.name),
    };
    rest.sort(by[sortKey]);
    return sortKey === "name" ? [...known, ...rest].sort(by.name) : [...known, ...rest];
  }, [results, sortKey, sourceFilter]);

  const catalogRow = (addon: AddonRow, highlight = false) => {
    const status = statusOf(addon);
    const already = !!status?.installed_version;
    const unsupported = !addon.error && !status?.supported;
    return (
      <Row key={addon.id} title={addon.name} subtitle={addon.summary} highlight={highlight} onOpen={openDetails(addon.id, addon.name, !already && !unsupported && !locked)}>
        {unsupported ? (
          <Muted>Not for {flavor?.label}</Muted>
        ) : (
          <Button label={already ? "Installed" : "Install"} disabled={locked || already} onClick={() => onInstall(addon.id, addon.name)} />
        )}
      </Row>
    );
  };

  const browseView = (
    <div>
      <Focusable style={{ display: "flex", gap: "10px", alignItems: "center", margin: "12px 0 4px" }}>
        <div style={{ flex: 1 }}>
          <TextField value={query} onChange={(event) => setQuery(event.target.value)} />
        </div>
        {query.length > 0 && <Button label="Clear" onClick={() => setQuery("")} />}
      </Focusable>
      <Focusable style={{ display: "flex", alignItems: "center", gap: "10px", margin: "0 2px 8px" }}>
        <div style={{ flex: 1, fontSize: "12px", opacity: 0.55 }}>
          {searching ? "Searching…" : visibleResults ? `${visibleResults.length} result${visibleResults.length === 1 ? "" : "s"}` : "Search the catalog and WoWInterface by name"}
        </div>
        {results && (
          <>
            <div style={{ width: "190px" }}>
              <Dropdown rgOptions={SORT_OPTIONS} selectedOption={sortKey} onChange={(option) => setSortKey(option.data as SortKey)} />
            </div>
            <div style={{ width: "170px" }}>
              <Dropdown rgOptions={SOURCE_OPTIONS} selectedOption={sourceFilter} onChange={(option) => setSourceFilter(option.data as SourceFilter)} />
            </div>
          </>
        )}
      </Focusable>

      {visibleResults ? (
        visibleResults.length === 0 && !searching ? (
          <div style={{ opacity: 0.7, padding: "12px 2px" }}>Nothing matched. Addons published only on CurseForge aren't available here.</div>
        ) : (
          visibleResults.map(resultRow)
        )
      ) : (
        <>
          <Heading sub="Controller support and the pieces that make WoW comfortable on a Deck.">Recommended for Steam Deck</Heading>
          {recommended.map((addon) => catalogRow(addon, true))}
          <Heading sub="Well-known addons with verified sources.">Popular</Heading>
          {catalog.map((addon) => catalogRow(addon))}
          <div style={{ marginTop: "18px" }}>
            {showRepoField ? (
              <Focusable style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                <div style={{ flex: 1 }}>
                  <TextField value={repoDraft} onChange={(event) => setRepoDraft(event.target.value)} />
                </div>
                <Button label="Add" disabled={locked || repoDraft.trim().length === 0} onClick={onAddRepo} />
                <Button label="Cancel" onClick={() => setShowRepoField(false)} />
              </Focusable>
            ) : (
              <Focusable style={{ ...rowStyle, justifyContent: "space-between" }}>
                <div style={{ fontSize: "13px", opacity: 0.8 }}>Have an addon on GitHub that isn't listed? Add it by repo name, e.g. funkydude/BadBoy.</div>
                <Button label="Add repo" onClick={() => setShowRepoField(true)} />
              </Focusable>
            )}
          </div>
        </>
      )}
    </div>
  );

  // ------------------------------------------------------------ frame

  if (listing && !listing.wow) {
    return (
      <div style={{ marginTop: "40px", padding: "24px 2.8vw", color: "#fff", opacity: 0.85 }}>
        World of Warcraft was not found in the Battle.net prefix. Install it through Battle.net first, then come back here.
      </div>
    );
  }

  return (
    <div style={{ marginTop: "40px", height: "calc(100% - 40px)", display: "flex", flexDirection: "column", color: "#fff" }}>
      <Focusable style={{ display: "flex", alignItems: "center", gap: "12px", padding: "6px 2.8vw 0" }}>
        <div style={{ fontSize: "20px", fontWeight: 700, flex: 1 }}>WoW addons</div>
        {listing?.wow && listing.wow.flavors.length > 1 ? (
          <div style={{ minWidth: "200px" }}>
            <Dropdown
              rgOptions={listing.wow.flavors.map((entry) => ({ data: entry.dir, label: `${entry.label} ${shortVersion(entry.version)}` }))}
              selectedOption={flavorDir}
              onChange={(option) => setFlavorDir(option.data as string)}
            />
          </div>
        ) : (
          flavor && <div style={{ fontSize: "13px", opacity: 0.7 }}>{`${flavor.label} ${shortVersion(flavor.version)}`}</div>
        )}
      </Focusable>
      {gameRunning && (
        <div style={{ margin: "8px 2.8vw 0", padding: "8px 12px", borderRadius: "6px", background: "rgba(255, 180, 0, 0.15)", fontSize: "13px" }}>
          World of Warcraft is running. Close it before installing, updating or removing addons.
        </div>
      )}
      {error && <div style={{ margin: "8px 2.8vw 0", color: "#ff8080" }}>Backend error: {error}</div>}
      {!listing && !error ? (
        <div style={{ display: "flex", justifyContent: "center", padding: "40px" }}>
          <SteamSpinner />
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0 }}>
          <Tabs
            activeTab={tab}
            onShowTab={(id: string) => setTab(id as "installed" | "browse")}
            tabs={[
              {
                id: "installed",
                title: updates.length > 0 ? `Installed (${updates.length} update${updates.length === 1 ? "" : "s"})` : "Installed",
                content: <div style={{ padding: "0 2.8vw 24px" }}>{installedView}</div>,
              },
              { id: "browse", title: "Browse", content: <div style={{ padding: "0 2.8vw 24px" }}>{browseView}</div> },
            ]}
          />
        </div>
      )}
    </div>
  );
}
