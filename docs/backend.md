# Backend language

Status: decided 2026-11-04. This page is kept identical
in Munki Web Studio and Cimian Web Studio.

## Decision

Python with FastAPI, for both studios. TypeScript was the alternative; .NET
was not recommended. FastAPI was chosen over Django on 2026-11-04: the
studios are API-first with a React workspace, need OpenAPI contracts for the
browser and for FleetMate, and must mount as routers inside a host app, which
FastAPI does natively. Sign-in, sessions, and migrations are assembled
deliberately instead of coming from Django. The same Python package also carries the
FastMCP server and the Typer command line (see `mcp.md` and `cli.md`).

## What decides it

The backend's hardest job is not serving web pages, which every candidate does
well. It is editing repository files without damaging them. An admin reviews a
pkginfo change as a Git diff, or trusts that a save did what the form showed. If
changing one display name rewrites forty lines, drops a comment, turns `"2.10"`
into `2.1`, or reads a hash of zeros as the number `0`, the tool has broken trust
in the one thing it exists to do.

So the choice was tested, not argued. One set of invented Munki and Cimian files
(XML, binary, and hand-edited plists; Munki and Cimian YAML with comments, quoted
versions, scripts, Windows paths, and integers too large for JavaScript) went
through a harness written in each language. Each harness parsed the files,
rewrote them, made one edit, and counted changed lines. The results are in the
[codec spike summary](codec-spike.md).

## What the test found

**No language gets this right with its libraries alone.** Every candidate
rewrote the hand-formatted plist in full and lost its comment. So every studio
needs a _surgical editor_: it finds the edited value's exact position in the file,
replaces only those bytes, and re-parses to prove the result. With that editor, all
three candidates reached the ideal diff on every text fixture. The editor is part
of the adapter whatever the language, so the question becomes: which language
makes the editor smallest and the rest of the codec safest?

|                                          | Python                   | TypeScript                   | .NET                                     |
| ---------------------------------------- | ------------------------ | ---------------------------- | ---------------------------------------- |
| Extra codec code                         | About 175 lines          | About 240 lines              | About 390 lines                          |
| Plist written exactly like Munki's tools | Yes, standard library    | No, needs its own writer     | No, 2 to 4 lines off                     |
| YAML read without type traps             | Yes, with three settings | No: `hash: 000…0` became `0` | No: every value a string, own type rules |
| Integers above 2^53                      | Native                   | Needs `bigint` everywhere    | Native                                   |
| Comments kept by the library             | Yes                      | Text kept, spacing lost      | Lost in the model                        |

## Why Python

1. **Smallest codec, least to get wrong.** The codec is where data loss happens.
   Python needs less than half the extra code .NET needs, and fewer special
   cases than TypeScript.
2. **Its plist library is the Munki reference.** Python's `plistlib` writes the
   layout Munki's own Python tools have always written, so a file those tools
   produced comes back byte-identical. Other languages must imitate that layout.
3. **No silent corruption found.** The one silent data change in the test, a
   zero hash read as a number, happened in TypeScript. Python's YAML library
   kept it as written, along with quoted versions and comments.
4. **Big numbers just work.** Python integers have no size limit, so file sizes
   and other large values need no special handling on the server.
5. **The web side is mature.** FastAPI, OpenID Connect sign-in, database
   migrations, background jobs, and OpenAPI contracts for the browser are all
   well-trodden.
6. **It runs the same everywhere.** One container image on Linux, macOS, or a
   Windows host with Docker. The Mac and Windows tools run as separate worker
   programs in any language, so no candidate gets an advantage there.

## Why both studios use the same language

Each studio could pick its own language and still run alone. But FleetMate Web
Admin hosts both studios in one process, and that only works if both are Python
packages. Copying shared behavior between the studios is also far easier when
the copies are in the same language. One choice, made once, serves all three.

## The case for the others

**TypeScript** would mean one language from browser to server, with types shared
directly. That is a real advantage, and the gap in codec size is modest. It loses
on safety: it must own its XML plist reader and writer, and guard every number it
parses. Generating TypeScript types from the Python API's OpenAPI schema recovers
most of the shared-types benefit.

**.NET** looked attractive for Cimian, which is written in C#, and for Windows
admins who host on IIS. The test removed the main reason: Cimian's own YAML code
does not round-trip (it reorders keys, drops comments and unknown keys, and
unquotes `"2.10"`), so there is nothing safe to reuse for writing. It also needs
the most extra code, and its plist library is the weakest of the three.

## What would change the answer

- A requirement that the backend run as a native Windows service with no
  container. That would favor .NET.
- A decision that sharing code with the browser matters more than codec size.
  That would favor TypeScript.
- A future Munki or Cimian release that changes file formats in a way one
  library handles much better.
