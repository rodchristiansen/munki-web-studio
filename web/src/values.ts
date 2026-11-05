// Read and display decoded tagged values (see tagged.ts).
import { Data, DateTime, Day, Real, type Value } from "./tagged";

export type Dict = Map<string, Value>;

export function asDict(value: Value | undefined): Dict {
  return value instanceof Map ? value : new Map();
}

export function show(value: Value | undefined): string {
  if (value === undefined || value === null) return "Not set";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "bigint") return value.toString();
  if (value instanceof Real) return String(value.value);
  if (value instanceof DateTime || value instanceof Day) return value.iso;
  if (value instanceof Data) return `${Math.floor((value.base64.length * 3) / 4)} bytes of data`;
  if (Array.isArray(value)) return value.map(show).join(", ");
  return [...value].map(([k, v]) => `${k}: ${show(v)}`).join(" · ");
}

export function items(value: Value | undefined): string[] {
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value.map(show) : [show(value)];
}
