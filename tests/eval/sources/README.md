# Eval sources — verbatim upstream files

Each directory here is one eval case built from a **real commit that introduced a
real defect**, rather than from an inverted fix. `before/` and `after/` hold the
upstream files at that commit's parent and at the commit itself. The harness
diffs them, so the case presents exactly the diff the upstream reviewer saw.

The files are **unmodified** apart from `#!EXPECT` / `#!ALLOW` / `#!CLEAN` marker
comments inserted into `after/`, which `scripts/build_eval_fixtures.py` strips
before building the diff. They live on disk rather than inline in `cases.py`
because they run to hundreds of lines each.

To find more, use `scripts/mine_introducers.py` — give it a fix and it walks back
to the commit that introduced what the fix removed.

## Attribution

These are third-party sources, reproduced under their own licenses for the
purpose of testing a code reviewer against them.

| directory | upstream | commit | license |
|---|---|---|---|
| `intro-tornado-multipart` | [tornadoweb/tornado](https://github.com/tornadoweb/tornado) | `9e965556` | Apache-2.0 |
| `intro-tornado-cookies-move` | [tornadoweb/tornado](https://github.com/tornadoweb/tornado) | `4a4d8717` | Apache-2.0 |
| `intro-urllib3-util-refactor` | [urllib3/urllib3](https://github.com/urllib3/urllib3) | `d8ff66d0` | MIT |
| `intro-requests-poolmanager` | [psf/requests](https://github.com/psf/requests) | `92d57036` | Apache-2.0 |

Every defect labelled here was found and fixed by the upstream project itself,
and the label cites the upstream fix. Nothing here is a criticism of these
projects — they are here precisely *because* they are well-maintained code in
which a subtle bug survived review, which is the situation this reviewer is
meant to help with.
