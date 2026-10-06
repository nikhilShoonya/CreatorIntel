import { Loader2 } from "lucide-react";

export default function Loading() {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4">
      <Loader2 size={36} className="animate-spin text-accent" />
      <p className="text-sm font-medium text-slate-500">Loading...</p>
    </div>
  );
}
