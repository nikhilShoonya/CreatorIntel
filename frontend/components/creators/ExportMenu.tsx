"use client";

import { useState } from "react";
import { ChevronDown, Download, FileSpreadsheet, FileText, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/controls";
import { Menu } from "@/components/ui/Menu";
import { api } from "@/lib/api";
import type { CreatorFilters } from "@/types";

export function ExportMenu({ filters, disabled, onError }: { filters: CreatorFilters; disabled?: boolean; onError: (message: string) => void }) {
  const [busy, setBusy] = useState(false);

  async function run(kind: "excel" | "csv") {
    setBusy(true);
    try {
      await api.download(kind, filters);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Menu
      label="Export"
      width={180}
      trigger={(props) => (
        <Button variant="soft" disabled={disabled || busy} {...props}>
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
          Export
          <ChevronDown size={14} />
        </Button>
      )}
      items={[
        { label: "Export Excel", icon: <FileSpreadsheet size={15} className="text-emerald-600" />, onSelect: () => run("excel") },
        { label: "Export CSV", icon: <FileText size={15} className="text-slate-500" />, onSelect: () => run("csv") },
      ]}
    />
  );
}
