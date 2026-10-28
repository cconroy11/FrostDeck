import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { type ReactNode, useState } from "react";
import { FaChevronDown, FaChevronRight } from "react-icons/fa";

/**
 * A collapsible panel section. The header row shows the title and, when
 * closed, a one-line summary, so the whole panel reads at a glance and only
 * the section being worked on takes space. Open/closed state is remembered
 * per section across panel opens (and across Steam restarts when storage is
 * available).
 */

const STORAGE_KEY = "frostdeck.sections";
const memory: Record<string, boolean> = {};

function stored(): Record<string, boolean> {
  try {
    const raw = window.localStorage?.getItem(STORAGE_KEY);
    return { ...(raw ? (JSON.parse(raw) as Record<string, boolean>) : {}), ...memory };
  } catch {
    return { ...memory };
  }
}

function remember(id: string, open: boolean): void {
  memory[id] = open;
  try {
    window.localStorage?.setItem(STORAGE_KEY, JSON.stringify(stored()));
  } catch {
    // Storage can be unavailable inside the Steam UI; the in-memory copy still works.
  }
}

export function Section(props: {
  id: string;
  title: string;
  summary?: ReactNode;
  defaultOpen?: boolean;
  /** Always open and not toggleable, e.g. while the Battle.net install flow is in progress. */
  locked?: boolean;
  children?: ReactNode;
}) {
  const { id, title, summary, defaultOpen = true, locked = false, children } = props;
  const [open, setOpen] = useState<boolean>(() => (locked ? true : (stored()[id] ?? defaultOpen)));
  const isOpen = locked || open;

  const toggle = () => {
    if (locked) {
      return;
    }
    setOpen((current) => {
      remember(id, !current);
      return !current;
    });
  };

  return (
    <PanelSection>
      <PanelSectionRow>
        <Field
          label={<span style={{ fontWeight: 600, fontSize: "15px" }}>{title}</span>}
          description={isOpen ? undefined : summary}
          focusable={!locked}
          highlightOnFocus={!locked}
          onActivate={toggle}
          onClick={toggle}
          childrenContainerWidth="min"
          bottomSeparator={isOpen ? "standard" : "none"}
        >
          {!locked && <span style={{ opacity: 0.7, display: "flex" }}>{isOpen ? <FaChevronDown /> : <FaChevronRight />}</span>}
        </Field>
      </PanelSectionRow>
      {isOpen && children}
    </PanelSection>
  );
}
