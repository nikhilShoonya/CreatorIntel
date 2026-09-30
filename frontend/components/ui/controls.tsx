import type { ButtonHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";

type Variant = "primary" | "secondary" | "ghost" | "soft" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white shadow-sm hover:bg-accent-strong disabled:bg-indigo-300",
  secondary: "border border-line bg-white text-slate-700 shadow-xs hover:bg-slate-50 disabled:text-slate-400",
  soft: "border border-indigo-100 bg-accent-soft text-accent hover:bg-indigo-100 disabled:opacity-60",
  ghost: "text-slate-600 hover:bg-slate-100 disabled:text-slate-300",
  danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-700 disabled:bg-rose-300",
};

export function Button({
  variant = "secondary",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; children: ReactNode }) {
  return (
    <button
      type="button"
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Select({
  label,
  options,
  value,
  onChange,
  className = "",
  ...props
}: Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange"> & {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const active = value !== "";
  return (
    <div className={`relative ${className}`}>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`h-9 w-full cursor-pointer appearance-none rounded-lg border bg-white py-0 pl-3 pr-8 text-sm shadow-xs outline-none transition-colors focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-indigo-100 ${
          active ? "border-indigo-200 text-accent" : "border-line text-slate-700"
        }`}
        {...props}
      >
        <option value="">{label}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown size={15} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)] ${className}`}>{children}</section>;
}

export function EmptyState({ icon, title, description, action }: { icon: ReactNode; title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-accent-soft text-accent">{icon}</div>
      <h3 className="text-base font-semibold text-ink">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-muted">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-center justify-between gap-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="font-medium underline underline-offset-2">
          Try again
        </button>
      )}
    </div>
  );
}
