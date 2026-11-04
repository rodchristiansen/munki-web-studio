# Codec spike summary

Run 2026-10-20 against invented Munki and Cimian files. This studio's share of
those files is in `tests/fixtures/`. The harnesses and per-language results live
in the FleetMate Web Admin repository under `spikes/codec/`.

## Finding that holds for every candidate

No library rewrite is good enough on its own. Every candidate rewrote the
hand-formatted plist in full (25 to 59 changed lines) and lost its comment. So
the adapter edits files **surgically**: it uses the parser's source positions to
replace only the changed value's bytes, then re-parses to verify. All three
candidates reached the ideal diff (2 lines for a value change, 1 for an append)
on every text fixture this way, with comments intact. Binary plists are rewritten
whole, which is fine because nobody reviews binary diffs line by line.

This makes the surgical patcher part of the adapter contract, whatever the
language.

## Comparison

|                                          | Python                                   | .NET                                     | TypeScript                                      |
| ---------------------------------------- | ---------------------------------------- | ---------------------------------------- | ----------------------------------------------- |
| Ideal diffs with surgical patch          | All                                      | All                                      | All                                             |
| Library rewrite of plistlib-format plist | Byte-identical                           | 2 to 4 lines off                         | Needs own writer                                |
| Library YAML round trip                  | Clean with settings                      | Drops comments or blank lines            | Loses comment spacing                           |
| Type traps found                         | None after settings                      | Every scalar a string; own type resolver | `hash: 000…0` read as `0`; big ints need bigint |
| Extra adapter code                       | About 175 lines                          | About 390 lines                          | About 240 lines                                 |
| Reuse of existing code                   | plistlib is Munki's own writer format    | Cimian's YAML writer does not round-trip | Tagged JSON shared with the React UI            |
| Libraries                                | plistlib (PSF), ruamel.yaml 0.19.1 (MIT) | YamlDotNet 18.1.0, plist-cil 2.3.1 (MIT) | yaml 2.9.1 (ISC), bplist-parser/creator (MIT)   |

## Reading

- **Python** needs the least code and is the only candidate whose libraries
  already write Munki's plist layout exactly. Its YAML library needs three
  settings fixed in the adapter.
- **TypeScript** is close behind and gives one language from browser to server,
  but it must own its XML plist codec and guard YAML number parsing, where a
  silent `hash` corruption was found.
- **.NET** works but needs the most code, and the main reason to pick it, reusing
  Cimian's code, did not hold: that writer reorders keys, drops comments and
  unknown keys, and unquotes `"2.10"`.

Recommendation: Python, with TypeScript as the alternative if one language
across the stack matters more than adapter size. Not decided until accepted.
