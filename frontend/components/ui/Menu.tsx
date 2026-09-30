"use client";

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";

export interface MenuItem {
  label: string;
  icon?: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  hint?: string;
  danger?: boolean;
}

interface MenuProps {
  trigger: (props: {
    onClick: (event: ReactMouseEvent<HTMLElement>) => void;
    "aria-expanded": boolean;
    "aria-haspopup": "menu";
  }) => ReactNode;
  items: MenuItem[];
  width?: number;
  label: string;
}

/**
 * Dropdown rendered with fixed positioning so it is never clipped by the
 * horizontally-scrolling table container.
 */
export function Menu({ trigger, items, width = 200, label }: MenuProps) {
  const [position, setPosition] = useState<{ top: number; left: number; anchor: HTMLElement; anchorTop: number; anchorLeft: number } | null>(null);
  const anchorRef = useRef<HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const open = position !== null;

  function toggle(event: ReactMouseEvent<HTMLElement>) {
    if (open) {
      setPosition(null);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const estimatedHeight = items.length * 38 + 12;
    const below = rect.bottom + 6;
    const top = below + estimatedHeight > window.innerHeight ? Math.max(8, rect.top - estimatedHeight - 6) : below;
    const left = Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8);
    setPosition({ top, left, anchor: event.currentTarget, anchorTop: rect.top, anchorLeft: rect.left });
  }

  useEffect(() => {
    if (!position) return;
    const close = () => setPosition(null);
    // Close only when the trigger actually moved (a stray scroll event must not dismiss the menu).
    const onScroll = () => {
      const rect = position.anchor.getBoundingClientRect();
      if (Math.abs(rect.top - position.anchorTop) > 2 || Math.abs(rect.left - position.anchorLeft) > 2) close();
    };
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !anchorRef.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", close);
    menuRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", close);
    };
  }, [position]);

  return (
    <>
      <span ref={anchorRef} className="inline-flex">
        {trigger({ onClick: toggle, "aria-expanded": open, "aria-haspopup": "menu" })}
      </span>
      {position && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={label}
          style={{ top: position.top, left: position.left, width }}
          className="fixed z-50 rounded-lg border border-line bg-white p-1 shadow-lg shadow-slate-900/10"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              title={item.hint}
              onClick={() => {
                setPosition(null);
                item.onSelect();
              }}
              className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm outline-none disabled:cursor-not-allowed disabled:text-slate-300 ${
                item.danger
                  ? "text-rose-600 hover:bg-rose-50 focus-visible:bg-rose-50"
                  : "text-slate-700 hover:bg-slate-50 focus-visible:bg-slate-50"
              }`}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
