import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-zinc-800 bg-zinc-900/60 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-emerald-500 text-zinc-950 hover:bg-emerald-400 disabled:bg-emerald-900 disabled:text-zinc-400",
  secondary: "bg-zinc-800 text-zinc-100 hover:bg-zinc-700 disabled:text-zinc-500",
  ghost: "text-zinc-300 hover:bg-zinc-800 disabled:text-zinc-600",
  danger: "text-red-300 hover:bg-red-950",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
}) {
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
    />
  );
}

const BADGE: Record<string, string> = {
  green: "bg-emerald-950 text-emerald-300 ring-emerald-800",
  yellow: "bg-amber-950 text-amber-300 ring-amber-800",
  red: "bg-red-950 text-red-300 ring-red-800",
  blue: "bg-sky-950 text-sky-300 ring-sky-800",
  purple: "bg-fuchsia-950 text-fuchsia-300 ring-fuchsia-800",
  gray: "bg-zinc-800 text-zinc-300 ring-zinc-700",
};

export function Badge({ color = "gray", children }: { color?: keyof typeof BADGE; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${BADGE[color]}`}>
      {children}
    </span>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-200">{message}</p>;
}

export const inputClass =
  "w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500 focus:outline-none";
