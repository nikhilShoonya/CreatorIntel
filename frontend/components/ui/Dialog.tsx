"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Loader2, X } from "lucide-react";

import { Button } from "./controls";

interface DialogProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}

/** Centered modal used for forms and confirmations. */
export function Dialog({ open, title, description, onClose, children, footer, size = "md" }: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.querySelector<HTMLElement>("input, button:not([data-close])")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  // Portal to <body> so the overlay always covers the whole page, even when opened from a sticky sidebar/header.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" data-close className="animate-fade-in absolute inset-0 bg-slate-900/40 backdrop-blur-[2px]" aria-label="Close" onClick={onClose} />
      <div
        ref={panelRef}
        className={`animate-modal-in relative flex max-h-[90vh] w-full flex-col rounded-2xl bg-white shadow-2xl shadow-slate-900/20 ${
          size === "sm" ? "max-w-md" : size === "lg" ? "max-w-3xl" : "max-w-lg"
        }`}
      >
        <header className="flex items-start justify-between gap-4 px-6 pb-2 pt-5">
          <div>
            <h2 className="text-lg font-semibold text-ink">{title}</h2>
            {description && <p className="mt-1 text-sm text-muted">{description}</p>}
          </div>
          <button type="button" data-close onClick={onClose} className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </header>
        {children && <div className="min-h-0 overflow-y-auto px-6 py-3">{children}</div>}
        {footer && <footer className="flex justify-end gap-2 px-6 pb-5 pt-3">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

interface ConfirmProps {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  children?: ReactNode;
}

/** Destructive confirmation (delete). Shows the server error inline if the action fails. */
export function ConfirmDialog({ open, title, message, confirmLabel, onConfirm, onClose, children }: ConfirmProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    if (busy) return;
    setError(null);
    onClose();
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} title={title} onClose={close} size="sm">
      <form onSubmit={confirm}>
        <div className="flex gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-rose-50 text-rose-600">
            <AlertTriangle size={18} />
          </span>
          <div className="text-sm text-slate-600">{message}</div>
        </div>
        {children}
        {error && <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
        <div className="mt-5 flex justify-end gap-2 pb-2">
          <Button onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" disabled={busy}>
            {busy && <Loader2 size={15} className="animate-spin" />}
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
