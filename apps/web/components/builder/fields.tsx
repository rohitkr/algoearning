"use client";

import { cn } from "@algoearning/ui";
import { type ReactNode, useId } from "react";

export const inputClass =
  "h-9 w-full rounded-lg border border-border bg-surface px-2.5 text-sm tabular-nums outline-none focus:border-primary focus:ring-1 focus:ring-primary aria-[invalid=true]:border-loss";

/** Label + control + inline error. The control gets the id and aria attributes through the render prop. */
export function Field({
  label,
  error,
  hint,
  className,
  children,
}: {
  label: string;
  error?: string;
  hint?: string;
  className?: string;
  children: (a: { id: string; "aria-invalid": boolean; "aria-describedby"?: string }) => ReactNode;
}) {
  const id = useId();
  const described = error ? `${id}-err` : hint ? `${id}-hint` : undefined;
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-medium text-muted">
        {label}
      </label>
      {children({ id, "aria-invalid": !!error, "aria-describedby": described })}
      {error ? (
        <p id={`${id}-err`} className="text-xs text-loss">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/** Number input that keeps the typed text while editing; an empty box becomes `empty` (null or NaN). */
export function NumberField({
  label,
  value,
  onChange,
  error,
  hint,
  step = 1,
  min,
  max,
  suffix,
  optional = false,
  className,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  error?: string;
  hint?: string;
  step?: number;
  min?: number;
  max?: number;
  suffix?: string;
  optional?: boolean;
  className?: string;
}) {
  return (
    <Field label={label} error={error} hint={hint} className={className}>
      {(a) => (
        <div className="relative">
          <input
            {...a}
            type="number"
            inputMode="decimal"
            className={cn(inputClass, suffix && "pr-9")}
            value={value == null || Number.isNaN(value) ? "" : value}
            step={step}
            min={min}
            max={max}
            placeholder={optional ? "Off" : undefined}
            onChange={(e) =>
              onChange(e.target.value === "" ? (optional ? null : Number.NaN) : e.target.valueAsNumber)
            }
          />
          {suffix && (
            <span className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-xs text-muted">
              {suffix}
            </span>
          )}
        </div>
      )}
    </Field>
  );
}

export function TimeField({
  label,
  value,
  onChange,
  error,
  min,
  max,
  className,
}: {
  label: string;
  value: string | undefined;
  onChange: (v: string) => void;
  error?: string;
  min?: string;
  max?: string;
  className?: string;
}) {
  return (
    <Field label={label} error={error} className={className}>
      {(a) => (
        <input
          {...a}
          type="time"
          className={inputClass}
          value={value ?? ""}
          min={min}
          max={max}
          onChange={(e) => onChange(e.target.value.slice(0, 5))}
        />
      )}
    </Field>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  error,
  hint,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  onChange: (v: T) => void;
  error?: string;
  hint?: string;
  className?: string;
}) {
  return (
    <Field label={label} error={error} hint={hint} className={className}>
      {(a) => (
        <select {...a} className={inputClass} value={value} onChange={(e) => onChange(e.target.value as T)}>
          {options.map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}

/** Two or three mutually exclusive choices (BUY/SELL, CE/PE) as a radio group styled as a toggle. */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  tones,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  tones?: Partial<Record<T, string>>;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex h-9 rounded-lg border border-border p-0.5"
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={cn(
              "min-w-11 rounded-md px-2.5 text-xs font-semibold transition-colors",
              on
                ? (tones?.[o.value] ?? "bg-primary text-primary-foreground")
                : "text-muted hover:bg-surface-2",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Check({
  label,
  checked,
  onChange,
  error,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  error?: string;
}) {
  return (
    <div>
      <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[var(--primary)]"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        {label}
      </label>
      {error && <p className="mt-1 text-xs text-loss">{error}</p>}
    </div>
  );
}
