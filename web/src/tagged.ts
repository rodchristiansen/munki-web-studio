// Lossless JSON for repository values. Mirrors src/<package>/tagged.py; both are
// held to tests/fixtures/tagged/vectors.json.

export class Real {
  constructor(readonly value: number) {}
}
export class DateTime {
  constructor(readonly iso: string) {}
}
export class Day {
  constructor(readonly iso: string) {}
}
export class Data {
  constructor(readonly base64: string) {}
}

export type Value =
  | null
  | boolean
  | string
  | number
  | bigint
  | Real
  | DateTime
  | Day
  | Data
  | Value[]
  | Map<string, Value>;

export type Tagged =
  | null
  | boolean
  | string
  | number
  | Tagged[]
  | { $type: "int" | "real" | "date" | "day" | "data"; value: string }
  | { $type: "dict"; value: [string, Tagged][] };

function isTag(value: unknown): value is { $type: string; value: unknown } {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === 2 && keys[0] === "$type" && keys[1] === "value";
}

export function decode(input: unknown): Value {
  if (input === null || typeof input === "boolean" || typeof input === "string") return input;
  if (typeof input === "number") {
    if (!Number.isSafeInteger(input)) throw new TypeError("Untagged numbers must be safe integers");
    return input;
  }
  if (Array.isArray(input)) return input.map(decode);
  if (!isTag(input)) throw new TypeError("Objects must be tagged with $type and value");
  const raw = input.value;
  switch (input.$type) {
    case "int":
      return BigInt(raw as string);
    case "real":
      return new Real(Number(raw as string));
    case "date":
      return new DateTime(raw as string);
    case "day":
      return new Day(raw as string);
    case "data":
      return new Data(raw as string);
    case "dict":
      return new Map((raw as [string, unknown][]).map(([k, v]) => [k, decode(v)]));
    default:
      throw new TypeError(`Unknown $type ${String(input.$type)}`);
  }
}

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

/** Format a real exactly as Python's repr(float) does, so both sides agree. */
export function realText(value: number): string {
  if (!Number.isFinite(value)) throw new TypeError("Infinite or NaN reals cannot be stored");
  if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";
  const sign = value < 0 ? "-" : "";
  // toExponential() with no argument gives the shortest digits that round-trip.
  const [mantissa = "", exponentText = "0"] = Math.abs(value).toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const exponent = Number(exponentText);
  if (exponent >= -4 && exponent < 16) {
    if (exponent < 0) return `${sign}0.${"0".repeat(-exponent - 1)}${digits}`;
    const whole = digits.slice(0, exponent + 1).padEnd(exponent + 1, "0");
    const fraction = digits.slice(exponent + 1);
    return `${sign}${whole}.${fraction || "0"}`;
  }
  const head = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
  const expSign = exponent < 0 ? "-" : "+";
  return `${sign}${head}e${expSign}${String(Math.abs(exponent)).padStart(2, "0")}`;
}

export function encode(value: Value): Tagged {
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new TypeError("Use bigint or Real for this number");
    return value;
  }
  if (typeof value === "bigint") {
    const abs = value < 0n ? -value : value;
    return abs <= MAX_SAFE ? Number(value) : { $type: "int", value: value.toString() };
  }
  if (value instanceof Real) return { $type: "real", value: realText(value.value) };
  if (value instanceof DateTime) return { $type: "date", value: value.iso };
  if (value instanceof Day) return { $type: "day", value: value.iso };
  if (value instanceof Data) return { $type: "data", value: value.base64 };
  if (Array.isArray(value)) return value.map(encode);
  return { $type: "dict", value: [...value].map(([k, v]) => [k, encode(v)] as [string, Tagged]) };
}
