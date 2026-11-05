// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { Data, Day, Real, type Value, decode, encode, realText } from "./tagged";

const vectorsPath = fileURLToPath(new URL("../../tests/fixtures/tagged/vectors.json", import.meta.url));
const vectors = JSON.parse(readFileSync(vectorsPath, "utf-8")) as { name: string; tagged: unknown }[];

describe("tagged JSON", () => {
  it.each(vectors.map((v) => [v.name, v.tagged] as const))("round-trips %s exactly", (_name, wire) => {
    expect(encode(decode(wire))).toEqual(wire);
  });

  it("keeps large integers, reals, data, and key order", () => {
    const value = decode({
      $type: "dict",
      value: [
        ["10", { $type: "int", value: "9007199254740993" }],
        ["a", { $type: "real", value: "2.0" }],
        ["2", { $type: "data", value: "AA==" }],
        ["d", { $type: "day", value: "2026-10-20" }],
      ],
    }) as Map<string, Value>;
    expect([...value.keys()]).toEqual(["10", "a", "2", "d"]);
    expect(value.get("10")).toBe(9007199254740993n);
    expect(value.get("a")).toBeInstanceOf(Real);
    expect(value.get("2")).toBeInstanceOf(Data);
    expect(value.get("d")).toBeInstanceOf(Day);
  });

  it("formats reals like Python", () => {
    const cases: [number, string][] = [
      [2, "2.0"], [2.5, "2.5"], [-0, "-0.0"], [1e-300, "1e-300"], [1e16, "1e+16"],
      [123456789, "123456789.0"], [0.0001, "0.0001"], [0.00001, "1e-05"], [1.5e300, "1.5e+300"],
      [9999999999999998, "9999999999999998.0"], [-123.456, "-123.456"],
    ];
    for (const [value, text] of cases) expect(realText(value)).toBe(text);
  });

  it("rejects malformed input", () => {
    expect(() => decode({ name: "untagged" })).toThrow(/tagged/);
    expect(() => decode({ $type: "nope", value: "1" })).toThrow(/Unknown/);
    expect(() => decode(1.5)).toThrow(/safe integers/);
  });
});
