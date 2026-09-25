# Measured results

Every number here is reproducible:

```bash
make eval-live                                         # one run
pr-sentinel eval --provider anthropic --repeat 3 --save arm.json
python scripts/compare_arms.py baseline.json arm.json  # which differences are real
```

Models: `claude-opus-5` (security, correctness), `claude-sonnet-5` (tests),
`claude-haiku-4-5` (docs). 63 cases, 73 required findings, 31 permitted, 21 traps.
Twelve are mined from merged fix commits, six from published security advisories,
four from the introducing commits of defects the inverted fixes had already
covered.

---

## The matcher was measuring the wrong thing

`agent_attribution` had sat at 0.33 for every run — only a third of findings came
from the specialist that should have found them — and it was reported in passing
several times without being investigated. It should have been.

Splitting the hits over three recorded runs:

| | count | share |
|---|---|---|
| expected agent, same concern | 92 | 35.7% |
| **different agent, different concern** | **146** | **56.6%** |
| different agent, same concern | 20 | 7.8% |

Only 7.8% was genuine cross-agent overlap. The majority were findings that landed
near a labelled line while talking about something else entirely — the tests agent
noting "no test covers this" on a line labelled for SQL injection, scored as a hit
on the injection. Labelled *security* defects were credited to correctness 36% of
the time, tests 28%, and security 21%.

**The architecture was fine; the metric was broken.** Two corrections:

- **A hit now requires the finding to be about the label's family of concern.**
  Landing nearby while discussing something else makes it unlabelled, which is
  what it is.
- **A trap fires only on the line it declares clean**, with no snapping tolerance.
  Twelve of thirteen recorded "false positives" were three to five lines away,
  aimed squarely at the defective function below the clean one and dragged onto it
  by the ±3 window. That tolerance is right for deciding whether a real defect was
  found and wrong for deciding whether a clean line was flagged.

Re-scored over the same recorded findings — the models were not re-run, only the
arithmetic changed:

| | before | after |
|---|---|---|
| agent attribution | 0.331 | **0.808** |
| category agreement | 0.387 | **0.947** |
| recall | 1.000 | 0.964 |
| precision strict | 0.983 | **0.402** |
| precision lenient | 0.983 | 0.879 |
| false positives per run | ~1 | 4.3 |

Recall barely moved, so the specialists really do describe almost every labelled
defect. **Precision was the inflated number** — 0.983 was measuring "did a finding
land near a labelled line", not "was it about the defect".

`scripts/rescore.py` exists because of this. Scoring has now changed four times,
and each time the recorded numbers quietly stopped meaning what the current code
would produce. Re-running the models to find out costs about ten dollars and
twenty minutes; the findings have not changed, only the arithmetic over them, so
re-scoring does it for nothing.

### Labelling what the models already find

0.402 strict against 0.879 lenient meant about sixty per cent of findings matched
no label. Those were mined out of the recorded runs — 71 distinct unlabelled
findings, 28 of which appeared in all three runs and so were worth a decision.

Judging them turned up something the set had no way to express. Six were **real
defects nobody had labelled**, including several worth having:

- renaming `--workers` to `--concurrency` breaks every existing invocation and any
  caller reading `args.workers` — a contract break the case only labelled as a
  documentation problem
- a wildcard CORS origin with `credentials: true` is *rejected by browsers*, so
  the configuration does not work at all; the case labelled the security problem
  and missed that it is also simply broken
- `csv_rows` joins raw values and never calls `csv_escape`, in a case written to
  be about test coverage

But fifteen were the tests agent correctly noting that a new function has no test,
on a line labelled for something else. **Neither `EXPECT` nor `CLEAN` was right.**
Requiring them would encode "always ask for tests" and penalise exactly the
restraint the system is built around; calling them false positives would label a
true statement a lie.

So the set gained a third kind of label:

```
EXPECT   a defect that must be found; missing it costs recall
ALLOW    a legitimate observation that is optional; it counts neither way
CLEAN    flagging this is a false positive
```

`ALLOW` findings are excluded from both precision denominators and from recall.
Making one costs nothing; not making one costs nothing.

Re-scored over the same recorded findings:

| | before labelling | after |
|---|---|---|
| precision strict | 0.402 | **0.653** |
| precision lenient | 0.879 | 0.898 |
| recall | 0.964 | 0.969 |
| agent attribution | 0.808 | **0.841** |
| unlabelled findings per run | 42.3 | **15.7** |

The set is now 53 required findings, 21 permitted, 21 traps. Strict precision
moved because the labels got more honest, not because the reviewer got better —
worth remembering when reading the number.

### The second pass, and what it found in my own labels

Going back for the remaining fifteen turned up almost nothing left to judge — and
instead five cases where the *label* was at fault. Three were correct findings
rejected on category:

- a traceback returned to a client, labelled `input_validation`, found as
  `error_handling` at confidence **0.99**
- `read_export_preview` skipping the confinement helper, labelled `injection`,
  found as `logic` at 0.98
- an unverified JWT yielding an attacker-controlled tenant id, labelled `authz`,
  found as `logic` at 0.92

All three are the same defect under a different fair reading. Labels can now name
alternatives — `category=input_validation|error_handling` — which is narrower than
dropping the family check and admits the ambiguity where it actually exists.

Four more were **real defects the set still had not labelled**:

- `_CACHE.clear()` on the size bound discards the entry just written, and every
  other tenant's
- `hash_reset_password` returns an MD5 digest where `hash_password` returns bcrypt,
  so nothing can verify a reset password against the stored format
- neither value quoted into `shell=True`, so a directory containing a space breaks
  the command with no attacker involved
- `apply_coupon` now requires `expires_at` on every coupon

### Stating a policy once instead of on every line

Nineteen of the `ALLOW` markers from the first pass said the same two things: the
tests agent noting new code has no test, the docs agent noting it is undocumented.
Those are legitimate on any diff and required on none, so they are now declared
once in the builder rather than scattered as markers. A `CLEAN` trap still
overrides them, because traps are checked first — `neg-clean-extract-method` still
penalises asking for a test on a documented, tested pure extraction.

| | after pass 1 | after pass 2 |
|---|---|---|
| precision strict | 0.653 | **0.898** |
| precision lenient | 0.898 | 0.937 |
| recall | 0.969 | 0.972 |
| category agreement | 0.947 | 0.908 |
| unlabelled findings per run | 15.7 | **2.0** |

Two unlabelled findings per run, from about eighty produced. The set now has an
opinion about substantially everything the reviewer says.

Read the direction of travel carefully: strict precision went 0.402 → 0.653 →
0.898 across two labelling passes in which **the reviewer did not change at all**.
Every one of those points came from the labels getting less wrong.

### What the honest precision number is telling us

0.402 strict against 0.879 lenient, with roughly 42 unlabelled findings per run,
means **about sixty per cent of what the system produces is not described by any
label**. Those are mostly legitimate observations the set does not cover: a real
missing test, a real undocumented parameter, on a line labelled for something
else. They are neither right nor wrong as far as the set is concerned.

That was the clearest direction for the set, and it is what the section above
acted on. Precision is still reported twice, and quoting the strict figure alone
would be as misleading as quoting 0.983 was.

---

## Current

63 cases, 73 required findings, 31 permitted, 21 traps. Mean over three runs,
$14.33 for the set. **As measured** — the numbers the run produced against the
labels that existed when it ran, which is the only column that can be compared
with anything:

```
                        mean      min      max
  precision strict     0.815    0.791    0.835
  precision lenient    0.986    0.986    0.986
  recall               0.991    0.986    1.000
  calibration error    0.084    0.076    0.093
  agent attribution    0.944    0.944    0.958
  category agreement   0.944    0.915    0.944
  false positives/run  1.000    1.000    1.000
  unlabelled/run      13.000   11.000   15.000
  cost per case       $0.076
```

Re-scored after the trap correction and the labelling pass, strict precision is
1.000, lenient 1.000, 0 false positives, 0 unlabelled. **That is not a headline
number and it is not comparable to anything** — see "Why strict precision stopped
meaning anything" below.

**72 of 73 required defects found on average** — two one-off misses across three
runs (`sec-command-injection`, `doc-misleading-name`), never the same label twice.
Strict precision at 1.000 is post-labelling; the measured figure before that pass
was 0.847. See "Full baseline — 63 cases" below.

## Mined from published security advisories

Mining ordinary merged fix commits produced six good cases and **not one security
defect**, because small fixes skew heavily towards correctness. Advisories are the
other end of that: every one is a security defect somebody took seriously enough
to publish, most link to the commit that fixed it, and the CWE is an independent
label for the class.

`scripts/mine_advisories.py` walks the GitHub advisory database for pip, npm and
Go, keeps advisories that reference a fixing commit in a permissively licensed
repository, and filters to small single-file changes. It surfaced 120 candidates,
40 distinct, 17 readable. Six were taken:

| case | advisory | the defect |
|---|---|---|
| `cve-jupyter-referer-token-log` | GHSA-c3mw-737p-c7g2 · CWE-532 | notebook URLs carry the auth token, so logging the Referer on a 5xx writes a live credential to the log |
| `cve-scrapy-s3-plaintext-default` | GHSA-76g3-c3x4-crvx · CWE-319 | `"https" if meta.get("is_secure") else "http"` selects plaintext whenever the key is absent, which is almost always |
| `cve-mdc-xss-xlink-href` | GHSA-mxm6-v9r6-r94c · CWE-79 | SVG `xlink:href` executes script exactly as `href` does and is missing from the sanitiser's list |
| `cve-zot-delete-maps-to-push` | GHSA-qg67-7m6v-qg25 · CWE-285 | every non-read method maps to `push`, so a push token authorises DELETE |
| `cve-perses-unvalidated-project-path` | GHSA-vr5f-w35q-98jp · CWE-22 | a query parameter reaches `filepath.Join` unvalidated |
| `cve-rclone-declared-length-allocation` | GHSA-2p48-j3qc-rx9f · CWE-789 | memory reserved from a client-declared Content-Length before any body is read |

All public, fixed and released. They are here because a reviewer that cannot catch
a CWE-79 or a CWE-285 is not much of a security reviewer, and nothing in the
hand-written set was testing either — the set had no XSS, no log leakage, no
scope confusion and no declared-length exhaustion at all.

The scrapy one is the pick of them. `"https" if request.meta.get("is_secure")
else "http"` is a line that reads as correct, and is a plaintext default.

**All six are found**, every run. Five were found by both the security and the
correctness agent; the scrapy plaintext default was found at confidence 0.99 with
the reasoning spelled out — *"S3 scheme now defaults to plaintext http when
is_secure is unset"*.

### And they exposed the attribution metric as broken

The first run of these six reported **agent attribution 0.000**: not one credited
to the security agent, on six cases mined from security advisories. That looked
like a serious finding about the panel.

It was a bug in the metric. Every one of the six came back as
`agreeing=[correctness + security]` — the security agent had found all of them.
The aggregator merges cross-agent findings and keeps the highest-confidence one as
primary, so the merged finding carries *that* agent's name and category, and
attribution was comparing the label against those alone.

A merged finding now carries every contributing agent **and** every contributing
category, and matching considers all of them. Re-scored over the same recorded
findings, attribution on those six went 0.000 → 1.000, and across the whole set
0.798 → 0.951. The number had been depressing every baseline in this file.

### Calibration only counts what the set has judged

The same run read `calibration error 0.107` with the 0.50–0.80 bins looking badly
overconfident. Those bins turned out to hold mostly *unlabelled* findings, scored
as failures — and an unknown is not a wrong answer. They cluster at low confidence
for the obvious reason: the reviewer is least sure about exactly the things nobody
has got round to labelling.

Calibration now uses only findings the set has an opinion about, a hit or a false
positive, which is the same rule already applied to permitted findings. The price
is that it is measured on the labelled subset — the part somebody understood well
enough to label — so it should be read next to the unlabelled count, which is
about 15 per run out of 83.



### A partial outage is worse than a total one

The 53-case baseline ran with the credit balance running out part way through the
third repeat. Runs one and two completed; run three lost 31 of 53 cases.

Nothing refused it. The guard added earlier only fires when *every* case loses its
panel, and because the summary reports the last run, the headline came back
reading **recall 0.349** for a reviewer that had just scored 0.968 twice. A total
outage announces itself; a partial one looks like a measurement.

The guard now refuses any run where more than a quarter of cases lose their whole
panel, on the grounds that the cases which did run are whichever ones got in
first. `rescore.py` drops such a run rather than averaging it in, which is how the
two good runs above were salvaged without paying for them again.

### Two harness bugs this run exposed

The first report of this run read **calibration error 0.276**, with the 0.60–0.80
bins showing 15–20% observed accuracy. The model had not changed. Permitted
findings were counting as "not a hit" in the calibration bins, and the tests and
docs observations they cover cluster at exactly those confidences. They are
excluded from precision because they are neither right nor wrong; they are no more
evidence about calibration. Excluding them: 0.276 → 0.076.

It also reported three false positives, and all three were the same artifact —
correct findings about the real defect whose line span also reached the clean
sibling three lines above:

> `search_customers` inlines values, violating `execute()`'s params contract —
> confidence 0.95, counted as a false positive for flagging a line labelled clean

Traps sit beside the defects they contrast with. A finding that reaches a labelled
defect is aimed at it, whatever framing it chose, so the trap check is now skipped
for those. False positives per run: 3 → 0.67.

Both were introduced by earlier changes of mine and neither showed up on the
34-case set — they needed the larger, harder corpus to surface.

---

---

## A/B: what counts as a `critical` security finding

`sec-cors-wildcard-credentials` was found in all six runs recorded up to this
point, rated `major` every time, and therefore auto-posted rather than escalated.
The data could not say whether the model was under-rating it or the label was
wrong; that was a judgement call, and it was made: a wildcard origin with
credentials lets any site read authenticated responses, so it is critical.

The guidance written from that is about the principle, not about CORS — a rule
naming one misconfiguration teaches nothing about the next one. Rate by what is at
stake if you are right, not by how many steps someone would need to take; and for
security, `critical` covers a control removed, weakened or bypassed, whether or
not an end-to-end exploit is demonstrated.

Three runs per arm, raw data in `baselines/ab-severity-{old,new}.json`:

| | old | new |
|---|---|---|
| the case's severity | `major`, `major`, `major` | **`critical` ×3** |
| the case's decision | `auto_post` ×3 | **`escalate` ×3** |
| precision strict | 0.987 | 0.983 |
| recall | 1.000 | 1.000 |
| negative controls | 12× suppress | 12× suppress |
| escalated cases, of 34 | 8.0 (7–9) | 9.7 (9–10) |
| cost per review | $0.070 | $0.071 |

Everything except the target moved within noise, and the four negative controls
stayed silent in all three runs, so the broadening did not leak into cases where
the right answer is nothing.

**The number to watch is escalations: 8.0 → 9.7 of 34.** Within noise at n=3, but
in the direction you would expect, and it is the real cost of the decision.
Broadening `critical` spends human queue capacity, which is the resource the whole
system exists to protect.

One wobble worth recording: `sec-path-traversal` wanted `escalate` and got
`auto_post` in one run of three, having escalated reliably before. One sample in
three is not a finding, but it is the kind of thing that becomes one.

---

## Fixing the incomplete citation — and correcting the claim

The 34-case set reported four missed labels in every run, all on
`tst-repeated-coverage-gap`. Written up as "a reviewer acting on that comment
fixes a third of the problem."

**That was wrong, and reading the comment shows it.** The body says:

> csv_rows, csv_escape, dump_streaming, dump_pretty, margins_for, and
> usable_width are all new public functions with no accompanying tests…

All six named. A reviewer is fully informed. What was incomplete was `evidence` —
the machine-readable record of where a finding applies, which is what is stored,
audited and read back weeks later by someone deciding whether the finding was
fair. Prose in `body` is not that record.

Which still matters, and there was a real bug underneath it: **the evidence list
was capped at five entries.** A finding covering six locations lost one silently,
so the case was unwinnable whatever the model did. The cap is now 12, with a unit
test.

With the cap raised, one instruction in the shared prompt — cite every location a
finding covers, not just the first — settled it. Three runs per arm on the
affected case:

| | before | after |
|---|---|---|
| evidence locations cited, per run | 1, 1, 3 | **6, 6, 6** |
| recall | 0.555 (0.333–1.000) | **1.000 (spread 0.000)** |

Raw data in `baselines/ab-evidence-citation-{old,new}.json`. The cap fix is
present in both arms, so the table isolates the prompt change.

### A failure the incident exposed

The full-set refresh after this change returned **zero findings, zero cost, and a
calibration error of 0.000** — which is also what a flawless run of a reviewer
that says nothing looks like. The cause was an exhausted API credit balance. The
run scored itself as a result and overwrote a good baseline on the way past; a
regression gate fed that report would have passed it.

The runner now refuses to score a run in which every agent failed in every case,
and saves nothing. Partial failure is still scored, because a degraded result is
a real result — only a total outage is refused.

---

## Third tranche — and how hard it is to write a clean case

Precision 0.983 and recall 1.000 meant the set had saturated again. Thirteen more
cases, attacking the two things it could not see.

**Defects invisible in the diff alone.** A migration that duplicates an index an
earlier migration already created; a retry default lowered from 5 to 1 where the
retrieved caller depends on the old value to survive a dependency that 503s one
call in three. Thirteen of the forty-seven cases now carry repository context that
the finding depends on, which is what retrieval is for and what almost nothing in
the set was testing.

**Whole cases that look alarming and are correct.** Dynamic SQL built by string
formatting from a closed allowlist. A refactor from `shell=True` to a list argv.
MD5 used to key a render cache. Every earlier trap was a clean sibling beside a
real defect, which is a weaker test than a change that reads as a vulnerability
from top to bottom and is not one.

Plus the severity boundary from both sides after ADR-0006 widened `critical` — a
deleted authorisation check against a removed rate limit and a leaked stack trace;
Go, SQL and a GitHub Actions workflow using `pull_request_target`; deletions, where
the defect is a guard that is gone rather than a line that is wrong; and one
defect buried in a forty-function mechanical rename.

| | before | after |
|---|---|---|
| cases | 34 | **47** |
| labelled findings | 37 | **47** |
| false-positive traps | 18 | **21** |
| cases needing repository context | ~3 | **13** |
| languages | 5 | **8** (adds Go, SQL, YAML) |

### The part worth reading

Writing a case with a planted bug is easy. Writing one that is genuinely clean is
much harder, and three rounds of trying failed:

- Round 1, eight trap hits. Inspection: *"directory passed to tar without `--`,
  allowing option injection"* — correct, a directory beginning with `-` is read by
  tar as an option. *"unknown sort raises KeyError instead of being rejected"* —
  correct, a 500 where a 400 belongs.
- Round 2, four. *"`f"/exports/{name}.tar.gz"` is still interpolated without
  validation"* — correct, a genuine path traversal I had left in.
  *"template and context concatenated without a delimiter"* — correct, `"ab"+"c"`
  and `"a"+"bc"` collide.
- Round 3, one. *"Test module never imports create_archive; both tests error"* at
  confidence 0.96 — simply true of the fixture I had written.

Each round the models were right and the label was wrong. Two conclusions came out
of it, and both are now in the harness.

**Traps can be scoped.** `#!CLEAN agent=security` claims only that flagging this
line as a security problem is a false positive, while observing that it has no
test is a legitimate thing to say. An unscoped `CLEAN` still claims that nothing
at all is a finding — 19 of the 21 traps keep that stronger promise, because a set
of only scoped traps stops testing restraint.

**Two of the negative controls were relabelled rather than defended.** They are
`trap-` rather than `neg-` now, with no gate expectation, because they are not
"nothing to find" cases: they test one specific reflex — MD5 is not always weak
crypto, `shell=False` is not command execution — while leaving other legitimate
observations available.

On the thirteen new cases, one run: precision 0.778 strict and 0.955 lenient,
recall 1.000. The gap between the two numbers is the design working — the
unlabelled findings behind it are, on inspection, mostly real.

**The full 47-case baseline is not recorded yet.** The API credit balance ran out
mid-run. The runner refused to score it, saved nothing, and left the previous
baseline intact — which is the guard added after the last time this happened
doing its job. The committed `baselines/anthropic-baseline.json` is therefore the
34-case one, and the numbers at the top of this file are its numbers.

---

## How the set grew

| | before | after |
|---|---|---|
| cases | 16 | **34** |
| labelled findings | 22 | **37** |
| false-positive traps | 5 | **18** |
| multi-file changes | 1 | **5** |
| cases where the answer is silence | 1 | **4** |
| languages | Python | Python, TypeScript, Terraform, TOML, Markdown |

The second tranche was written to be harder in three specific ways, because a set
where every labelled defect is obvious stops discriminating once a model gets good:

- **A real defect sits next to a plausible look-alike.** A path join with no
  confinement check, one function below one with `realpath` and a prefix test. A
  signature compared with `==`, one line below one compared with `compare_digest`.
  A mutable default argument beside a correct `None` sentinel. A retry decorator
  on a charge, beside a retry decorator on a read. Sixteen of the thirty-four
  cases now carry at least one trap.
- **Not everything is Python.** A Terraform bucket made world-readable by copying
  the public-assets block above it; wildcard CORS with credentials in TypeScript;
  a null dereference on a genuinely optional field.
- **More of them have nothing wrong.** Four negative controls, including a pull
  request that only adds good tests and a routine dependency bump — the shapes
  where a reviewer's credibility is actually spent.

---

## What the data says to do next

- **Precision has run out of room.** 0.992 on 15 cases with 16 labels means the
  set can no longer tell a good change from a great one. Every remaining question
  needs harder cases, not more tuning against these.
- **The tests agent still files one coverage comment per function.** Two attempts
  at instructing it otherwise have not worked. The next thing to try is structural
  rather than textual: have the aggregator collapse same-agent coverage findings
  across a pull request into one, which does not depend on the model complying.
- **The assertion-free test is found one run in three.** That is the clearest
  remaining quality gap and the one a larger set would let us fix with confidence.
- **Matching is location-based**, so a coverage complaint that happens to land on
  a line labelled for security counts as a hit. It inflates per-agent numbers for
  whichever agent comments most. Agent attribution (0.33) is the honest view; a
  family-aware matcher would be better.
- **The set is too small.** Run-to-run spread on an unchanged configuration is
  ±0.04 overall precision and ±0.15 for a single agent. Anything smaller than that
  cannot be measured here. More cases, not more repetitions.

---

## Mined from the introducing commits

Every case with a real provenance before this one is an **inverted fix**: I took
the upstream commit that repaired a defect and ran it backwards. It is a usable
proxy and it flatters the reviewer, for a reason worth stating plainly — the fix
already knows where the bug is. Its diff is centred on the defective line, so
inverting it produces a small, well-aimed patch in which the defect is most of
what changed. A reviewer does not get that. A reviewer gets the commit that
shipped the defect: a feature or a refactor, larger, with the defect one line
among many that all look equally plausible.

These four are those commits — the real upstream files at the real SHA, unedited
except for the label markers, which is why they live in `tests/eval/sources/`
rather than inline in `cases.py`.

| case | introducing commit | changed | the fix came |
|---|---|---|---|
| `intro-tornado-multipart` | `9e965556` *"Don't assume 'boundary' is last field in Content-Type header"* | 9 lines | 3 days later |
| `intro-tornado-cookies-move` | `4a4d8717` *"Move 'cookies' property from RequestHandler to HTTPRequest"* | 33 lines, 2 files | 3 months later |
| `intro-urllib3-util-refactor` | `d8ff66d0` *"Refactor helpers into util.py"* | 125 lines, new file | 7 weeks later |
| `intro-requests-poolmanager` | `92d57036` *"WHOOOOOOOOOOOOOOOO"* | 70 lines | same day |

Each is deliberately **paired** with the inverted-fix case for the same defect —
`real-tornado-multipart-boundary`, `real-tornado-cookie-none-guard`,
`real-urllib3-format-placeholder`, `real-requests-implicit-relative-import`. Same bug,
two framings. If the reviewer scores well on the inversion and badly on the real
commit, the inversion was flattering it, and the pair is what makes that visible.
That comparison is the point of the tranche and it has **not been run yet** — see
below.

Two of them are worth describing, because they are shapes the hand-written cases
do not contain:

- **`intro-urllib3-util-refactor` is a needle.** The commit creates a 125-line
  module by lifting helpers out of several others. Every line is an addition, so
  nothing in the diff is a signal — there is no small edit to draw the eye. The
  defect is `raise LocationParseError("Failed to parse: %s")`, a format
  placeholder with no argument, one line in 125.
- **`intro-tornado-cookies-move` spans two files and the broken caller is in
  neither diff hunk that contains the bug.** The commit adds an `HTTPRequest.cookies`
  property that sets `self._cookies = None` when the header will not parse. The
  code that breaks is `get_cookie` in `web.py`, which does
  `if name in self.request.cookies` — and that line is *not modified by the
  commit*, so it never appears as a changed line. Finding this requires reading
  the new property's contract against a caller the diff does not show.

### Finding them: `git log -S`, not `git blame`

Blaming the fix's parent is the obvious approach and it failed twice:

- **Merge SHAs mostly do not resolve.** GitHub's `merge_commit_sha` is a test-merge
  computed for the PR page; for older pull requests it was frequently never
  pushed, so a full clone has never heard of it.
- **Blame lands on reformatting.** Every one of these repositories has a commit
  like tornado's `e211ec0a` *"adding black formatter to all the code"* touching
  308 files, and blame correctly reports it as the last toucher of every line.
  Skipping those by subject and file count is a heuristic that needs re-tuning
  per repository and still misses smaller reindents.

`git log -S<content>` searches for commits that change the *number of occurrences*
of a string. A pure whitespace commit does not change that count, so it is
invisible to the search for free — no heuristic, no tuning. `scripts/mine_introducers.py`
is built on this and reproduces all four results deterministically from one
command. Scrapy could not be traced this way and was dropped: its defective line
is not distinctive enough to search on.

### Measured — and the prediction was wrong

I predicted, in this file before running it, that recall would drop on
`intro-urllib3-util-refactor` and `intro-tornado-cookies-move` and hold on the
other two. **It did not drop.** Recall was 1.000 on all four, on all three runs,
with a spread of 0.000 — including the 125-line needle and the two-file move.

Both sides of every pair, same panel, same day:

| | inverted fix | introducing commit |
|---|---|---|
| recall | 1.000 | 1.000 *(3 runs, spread 0.000)* |
| precision, lenient | 1.000 | 1.000 |
| precision, strict *(before labelling)* | 0.833 | 0.714 *(0.430 mean over 3 runs)* |
| precision, strict *(after labelling)* | 1.000 | 1.000 |
| agent attribution | 0.800 | 0.800 |
| category agreement | 0.600 | **1.000** |
| calibration error | 0.106 | 0.088 |
| cost per case | $0.065 | $0.155 |

So the inverted fixes were **not** flattering the reviewer on these four defects.
The hypothesis that motivated the whole tranche is not supported. That is worth
stating plainly, because the tranche was built to expose a weakness and did not
find one.

Two things it did find, neither of which was the point:

- **Category agreement is better on the real commits** (1.000 vs 0.600), not
  worse. On the inversions the panel more often gets the defect right and its
  name wrong. A plausible reading is that an inverted fix strips the surrounding
  code that would tell you what *kind* of bug it is, but with n=4 on each side
  that is a hypothesis, not a finding.
- **The real commits cost 2.4x more per case**, which is just diff size.

### The panel found a defect I had missed

Scoring flagged two unlabelled findings on the introducer cases. Both verified
against the upstream files:

- `requests/sessions.py:295` — the commit **deletes** `_send_request` while
  `old_request` still calls it. Defined at `:271` before, absent after, still
  called at `:295`. A second real defect in that commit, which I did not label
  when I authored the case and which the upstream fix never touched (the method
  is unreachable, so it is latent). Now `ALLOW`.
- `tornado/httpserver.py:360` — splitting the content-type on `";"` is not RFC
  2045 parsing, so a `boundary="..."` value keeps its quotes. Also real, and also
  not addressed by the upstream `.strip()` fix. Now `ALLOW`.

After labelling those and one equivalent on the inverted side, every finding the
panel produced on all eight cases is accounted for: 0 unlabelled, 0 false
positives, strict precision 1.000 on both sides.

That 1.000 is **post-hoc labelling, not an independent measurement**. The honest
pre-labelling numbers are the ones in the table above. What the labelling pass
legitimately establishes is that nothing the panel said on these eight cases was
wrong — which is a different and weaker claim than a strict precision of 1.000
read cold.

### What this leaves

Recall is 1.000 on both framings with zero spread, so neither framing can
separate configurations any more. Harder cases are still the only lever, and
"harder" now demonstrably does not mean "the real commit instead of the
inversion" — that knob is spent. The remaining untested axis is a defect whose
fix *nobody upstream has written yet*, which by construction cannot be mined
this way.

---

## Full baseline — 63 cases, 3 runs

$14.33 over three runs of the whole set ($4.77/run, $0.076/case, ~750 model calls).

| metric | 59-case baseline | 63 cases, as measured | after trap fix | after labelling the 13 |
|---|---|---|---|---|
| precision, strict | 0.819 | 0.815 *(0.791–0.835)* | 0.847 *(0.828–0.866)* | **1.000** |
| precision, lenient | 0.995 | 0.986 *(spread 0.000)* | 1.000 | **1.000** |
| recall | 1.000 *(spread 0.000)* | 0.991 *(0.986–1.000)* | 0.991 | 0.991 |
| calibration error | 0.088 | 0.084 *(0.076–0.093)* | 0.081 | 0.081 |
| agent attribution | 0.951 | 0.944 | 0.949 | 0.949 |
| category agreement | — | 0.944 | 0.930 | 0.930 |
| gate decision match | — | 1.000 | 1.000 | 1.000 |
| false positives | — | 1 | **0** | **0** |
| unlabelled / run | — | — | 13 | **0** |

**I predicted strict precision would rise, and as measured it did not** — 0.815
against 0.819, inside the ±0.044 spread either way. The three `ALLOW` labels
added earlier that day did retire findings which used to count against it, but
the four new introducer cases brought their own unlabelled findings and cancelled
the gain. The rise to 0.847 only appears after the trap correction below, and the
rise to 1.000 only after the labelling pass after that — both bookkeeping in
exactly the same sense. The reviewer did not get better.

### Recall is no longer pinned at 1.000

Two misses, in different runs, of different labels: `sec-command-injection` in
run 1, `doc-misleading-name` in run 2, none in run 3. One miss out of 73 labels
each time. That is not a systematic weakness, it is the set finally sitting just
below saturation — which makes it marginally more useful than it was, since a
metric stuck at 1.000 cannot report a regression. A missed command injection is
still the more interesting of the two and worth watching across future runs.

### The fourth negative control that wasn't clean

`trap-subprocess-list-args` produced the run's only false positive, in all three
runs, at confidence 0.55 — the security agent flagging that `directory` reaches
`tar` unvalidated. Checking it: the case passes
`["tar", "-czf", f"/exports/{name}.tar.gz", "--", directory]` with `shell=False`,
and my trap note claimed neither value "can become a shell token, a tar option or
a path escape". The first two hold. **The third does not.** `--` ends option
parsing; it places no constraint on the path, so a caller passing untrusted
`directory` archives whatever it points at. The model was right and the label was
wrong.

It was also not making the mistake the trap exists to catch. Its category was
`input_validation` in every run, never `injection` — it was not claiming shell
injection, which really is impossible here. The trap was scoped by agent alone,
so any security observation on that line fired it.

The trap is now scoped `category=injection`, and the three legitimate
observations on that case are labelled `ALLOW` after checking each:

- `directory` unconstrained by `--` *(security, input_validation)*
- `shell=True` used to word-split and glob `directory`; the list argv does not
  *(correctness, api_contract)*
- the test asserts only `shell is False`, never the argv list, so it would pass
  against a re-introduced f-string *(tests, test_quality)*

This is the **fourth** hand-written negative control to contain a real defect I
had not seen — after the path traversal, the string-concat collision and the
fixture importing nothing. The pattern is consistent enough to state as a rule:
a control written by the same person who writes the labels is not a control. The
scoped trap is the mitigation that has worked; the unscoped one has failed every
time.

### Labelling the remaining thirteen

Thirteen unlabelled findings per run were the whole of the gap between strict
(0.847) and lenient (1.000). Re-scored over the same recorded findings after
labelling — **$0**, no model calls:

Every one of them checked out. None were noise. They fell into two piles:

**Same defect, different family.** The matcher requires the finding's concern
family to agree with the label's, so a correctness `api_contract` reading of an
f-string SQL injection, or a correctness `logic` reading of an unscrubbed
Referer, sits next to the security `EXPECT` and does not hit it. Nine of the
thirteen were that shape — the panel describing the labelled defect under the
other specialist's vocabulary. Each is now `ALLOW` on the same line.

**Real secondary observations the set had not named.** Four were defects (or
real consequences) the labels had missed:

- `intro-requests-poolmanager` — `request()` still advertises `verify`/`cert`/
  `timeout` and then drops them on the floor; TLS policy the caller asked for is
  silently ignored
- `trap-md5-for-cache-key` — `json.dumps(..., sort_keys=True)` narrows accepted
  context values from hashable to JSON-serialisable and collides non-string keys
  (the trap only forbids the MD5-as-crypto reading)
- `intro-urllib3-util-refactor` — `get_host` ends the authority at `/` and splits
  on the first `@`, so query/fragment and multi-`@` userinfo diverge from
  `urlparse`
- `intro-tornado-cookies-move` — `RequestHandler.cookies` was a documented public
  property and left with no alias

Plus smaller but genuine side-effects on the advisory and hand-written cases: an
ignored `extra_param_keys` after the Referer scrubber was deleted; unknown HTTP
methods defaulting to `push` in zot; `contentLength=-1` reaching `Reserve`; the
workflow measuring the PR head rather than the merge result; `tenant_id` in an
internal URL path; the cache-clear being abuseable; the fixture's return shape;
`AttributeError` instead of `Forbidden` for unknown report ids; `==` vs
`compare_digest` on `None`/bytes; a PR that claims a rounding fix while only
deleting the test.

The set is now 73 required, 31 permitted, 21 traps. Strict precision at 1.000 is
**post-hoc labelling, not an independent measurement** — the honest pre-labelling
number on this run is 0.847. What the pass establishes is that nothing the panel
said across the three runs was wrong. That is a different and weaker claim than a
cold reading of 1.000.

Recall is unchanged at 0.991. The two one-off misses
(`sec-command-injection`, `doc-misleading-name`) were never going to move on a
rescoring pass.

---

## Why strict precision stopped meaning anything

The labelling pass took strict precision from 0.847 to 1.000 by examining the 13
unlabelled findings, confirming each one, and marking it `ALLOW`. Every label was
justified — I checked the load-bearing ones independently and they hold. That is
not the problem.

The problem is the loop. It runs:

    measure → whatever is unlabelled, verify it → label it ALLOW → precision 1.000

After any labelling pass, strict precision is 1.000 **by construction**. It cannot
report a bad result, because a finding can only stay unlabelled if nobody looked
at it. It has stopped measuring the reviewer and started measuring whether I have
done the labelling pass yet. A number that can only move one way is not a
measurement.

Three numbers survive this and should be the ones quoted:

- **Unlabelled findings per run, on cases whose labels predate the run.** This is
  the honest version of precision: how much does the panel say that nobody has
  yet agreed is worth saying. It was 13/run here. It is only meaningful *before*
  the labelling pass, which means it must be recorded at measurement time — as it
  now is, in the block above.
- **Lenient precision**, which only traps can move. Traps are written in advance
  and are not added in response to output, so this one is falsifiable. It caught a
  real regression this run (0.986) and the regression turned out to be my label,
  not the model.
- **Recall**, which a labelling pass cannot inflate, because every label added
  from the model's own output is `ALLOW` and `ALLOW` never counts as a hit.

### The rule that keeps recall honest

All 21 labels from this pass are `ALLOW`; `required` stayed at 73. That is
deliberate and it is the right call, though it was not stated at the time.

Several of the newly labelled findings are serious enough to deserve `EXPECT` —
`verify`/`cert` accepted by `Session.request` and then silently dropped, so a
caller asking for TLS verification does not get it, is a genuine security defect
a reviewer ought to be required to catch. But promoting a finding to `EXPECT`
*because a model produced it* makes recall self-fulfilling: the run that
discovered the label scores 1.000 on it automatically.

So: **a label derived from model output is `ALLOW`. A label may only become
`EXPECT` on independent evidence** — an upstream fix, an advisory, a reading of
the code done without the finding in hand. Several of these are good candidates
for promotion once someone confirms them that way; until then they stay `ALLOW`
and recall stays honest.

### The recorded run is immutable

The rescoring pass wrote its output back over `baseline-63.json`, replacing the
as-measured scores (18 unlabelled, 1 false positive in run 1) with the rescored
ones (0 and 0). The raw findings survived, but the record of *how that run
actually scored* did not — which would have made every later "compared against
the baseline" quietly compare against post-hoc relabelling.

The file is restored, and `scripts/rescore.py` now refuses to write over its own
input, with a test. Rescoring is a derived view of a measurement; it does not get
to edit the measurement.

### Auditing the 22 labels line by line

Every label from the labelling pass was checked against the case source rather
than against the note that came with it. **19 of 22 hold as written.** The
strongest are precise about mechanism rather than gesturing at one:

- `intro-urllib3-util-refactor` — the authority parser ends at `/` only and
  splits on the *first* `@`. Both halves check out and both flip the host:
  `http://evil.com?x=@good.com` has no `/`, so `?x=@good.com` stays in the
  authority and the `@` split yields `good.com` as the host, while a real parser
  sees `evil.com`; and `a@b@real.com` yields `b@real.com` where RFC 3986 takes
  userinfo to the last `@`. A host used for allow/deny decisions can be flipped
  either way.
- `trap-md5-for-cache-key` — `json.dumps(..., sort_keys=True)` narrows values
  from hashable to JSON-serialisable *and* coerces integer keys to strings, so
  `{1: "a"}` and `{"1": "a"}` collide, and mixed-type keys raise on comparison.
- `intro-requests-poolmanager` — `verify`, `cert`, `timeout`, `prefetch`,
  `return_response` and `config` are in `request()`'s signature and never
  assigned to the `Request` or passed to `send`.

**Three asserted more than the code supports.** The labels stay — each is still
something a reviewer could fairly say — but the notes were wrong and are now
corrected, because the note is the record of why the label exists:

- `sec-removed-authz-check` claimed an unknown report id "used to fail inside
  `require_owner`; now surfaces as AttributeError". It surfaced as AttributeError
  *before* too — `None.tenant_id` inside `require_owner`. The failure moves lines;
  the outcome is unchanged. The note said a non-change was a change.
- `cve-jupyter-referer-token-log` claimed "callers still pass it and silently get
  no effect" about `extra_param_keys`. The case is a fragment with no enclosing
  signature in view, so caller behaviour cannot be read off this diff. The
  dead-parameter reading is plausible, not established.
- `sec-timing-unsafe-compare` observed that `==` and `compare_digest` diverge on
  `None`/bytes inputs. True, but it is a property of the *replacement*, not a
  defect in the code under review.

None of the three changes a score — an `ALLOW` counts the same whatever its note
says. That is the point worth keeping: **the scores could not have caught this.**
A label with sound reasoning and a label with unsound reasoning are numerically
identical, so the only thing standing between a justified `ALLOW` and a rubber
stamp is somebody reading the source. Two passes, by two different models, both
produced notes that asserted more than the diff showed.

### The rerun that did not happen, and what it cost to learn

The corrected labels were never measured. The rerun reached 344 successful model
calls — run 1 of 3 complete, run 2 about a third in — and then the credit balance
ran out. 39 of 63 cases lost their whole panel, the guard refused, and **nothing
was saved.**

Including run 1, which had completed cleanly, with every case getting its whole
panel, at a cost of roughly $5. The guard raised, the exception propagated out of
the loop, and the completed run went with it.

That was wrong, and it is fixed. The guard's job is to stop a *partial* run being
scored as though it were complete. A run that already finished is not partial, and
an outage in run N says nothing about runs 1..N-1. `run_eval` now discards the
outage run, keeps what completed, logs `eval.run.outage`, and returns a spread
over fewer runs rather than over none. An outage in the *first* run still
propagates, because then there is genuinely nothing to report.

So the open question stands, unmeasured: **does unlabelled-per-run come back near
zero on a fresh sample, or near thirteen?** The prediction on record is 4–9 —
neither. If it is near zero the 22 labels generalise and strict precision means
something again; if it is near thirteen the labelling pass was fitting one sample
and strict precision will never converge. That is the experiment worth buying with
the next $15.

### Rerun against the corrected labels — the prediction was wrong again

$14.57, three runs, and the panel saw **byte-identical input** to the previous
baseline (the golden patches hash the same; label markers are stripped before the
diff is built, so notes and labels never reach a model).

| | previous baseline | this run |
|---|---|---|
| unlabelled / run | 18, 13, 15 | **2, 2, 0** |
| false positives / run | 1, 1, 1 | **0, 0, 0** |
| missed labels / run | 1, 1, 0 | **0, 0, 0** |
| precision, strict | 0.815 *(0.791–0.835)* | **0.982** *(0.973–1.000)* |
| precision, lenient | 0.986 | 1.000 *(spread 0.000)* |
| recall | 0.991 *(0.986–1.000)* | 1.000 *(spread 0.000)* |
| calibration error | 0.084 | 0.091 *(0.086–0.097)* |
| agent attribution | 0.944 | 0.946 |
| category agreement | 0.944 | 0.905 |
| raw findings / run | 163, 158, 158 | 157, 162, 161 |

**I predicted 4–9 unlabelled per run. It came back 0–2.** That is the third
prediction in this file to be wrong, and the second in a row in the same
direction — I keep expecting the reviewer to do worse than it does.

The result that matters: **strict precision of 0.982 is falsifiable.** The 22
labels predate this run, so nothing here was fitted to it, and unlabelled-per-run
could have come back at thirteen. It came back at 1.3 on average. The labels
generalise across samples; they were not chasing one.

The four unlabelled findings that did appear were on four different cases
(`intro-requests-poolmanager`, `tst-time-dependent-flaky`, `cor-check-then-act-race`,
`sec-terraform-public-bucket`), one each, none twice. That is a long tail, not a
gap.

Two things not to misread:

- **`findings produced` fell from 88 to 75, and that is not a behaviour change.**
  That row counts scoreable findings — hits + unlabelled + false positives.
  Previously 72 + 15 + 1; now 74 + 1 + 0. The raw findings the panel emitted are
  unchanged at ~160 per run. Nothing got quieter; the unlabelled pile moved into
  the labelled one.
- **Recall is pinned at 1.000 again**, spread 0.000, no misses in any run. The two
  one-off misses in the previous baseline (`sec-command-injection`,
  `doc-misleading-name`) did not recur, so they were noise rather than a weakness,
  and the set has gone back to being unable to report a recall regression.

Category agreement fell 0.944 → 0.905. Unexplained, and larger than it looks
comfortable to wave at; it is the one number here worth a look before it is
quoted as stable.

### A reporting gap this exposed

`--verbose` prints the unlabelled findings of the **final run only**. This run's
final run had none, so the block printed nothing at all while four unlabelled
findings sat in runs 1 and 2 — recoverable only from the saved JSON. The union
across runs is exactly what a labelling pass needs. Worth fixing before the next
one.

### Unlabelled findings are now reported across the whole repeat

`--verbose` printed the last run's unlabelled findings, which on this set meant
printing nothing while four sat in the two runs before it. `unlabelled_union`
now reports the union across every run, sorted by how often each recurred, and
`scripts/rescore.py --verbose` prints the same thing for free from a recorded
run — which is where a labelling pass should start, since it costs nothing.

The recurrence count is the part that earns its place. Run against the recorded
baseline, all four came back `[1/3]`:

```
UNLABELLED across 3 runs  (4 distinct)
  [1/3] cor-check-then-act-race      limits/ratelimit.py:14 [security] cost not checked non-negative before adjusting the quota (0.55)
  [1/3] intro-requests-poolmanager   requests/sessions.py:363 [correctness] send() builds a new HTTPAdapter per request, defeating pooling (0.75)
  [1/3] sec-terraform-public-bucket  infra/storage.tf:13 [security] invoices bucket declared without SSE or versioning (0.60)
  [1/3] tst-time-dependent-flaky     tests/test_tokens.py:12 [correctness] is_expired called without `now` (0.50)
```

A standing opinion in 3 of 3 runs is a hole in the label set; a 1-of-3 is a
one-off. All four are one-offs, which is consistent with unlabelled-per-run
sitting at 1.3.

One of them is worth recording whatever is decided about labelling: in
`intro-requests-poolmanager`, `send()` really does construct a fresh
`HTTPAdapter` on every call. In the same commit that deletes `init_poolmanager`
and reduces `close()` to `pass`, that is the connection pooling removed three
different ways. **These four are deliberately left unlabelled.** Labelling
findings as they appear is the loop that made strict precision unfalsifiable in
the first place, and doing it again immediately would undo the point of the run
that just demonstrated the labels generalise.

### Against the Genesis Kit checklist

Checked this project against the five failure modes Genesis Kit names. Two were
already handled, one does not apply, and two were real:

| Genesis failure mode | here |
|---|---|
| Memory loss — state only in context | already handled: RESULTS.md, `tests/eval/recorded/*.json`, golden fixtures, and an append-only event spine in the database |
| Self-grading — the builder judges its own work | **real, and previously identified.** Labels are written in response to model output, which is why strict precision became unfalsifiable. The `ALLOW`-only rule limits the damage to precision; recall stays honest. There is still no independent check on a label's reasoning — the audit that found three overstated notes was done by the same agent that wrote them |
| Duplication — rebuilding what exists | does not apply to this codebase |
| Narration over execution | **real, now partly fixed.** RESULTS.md is prose around hand-transcribed tables, where a mistyped digit is indistinguishable from a measurement. A test now recomputes the per-run counts from every recorded run and fails if the document does not state them |
| Untiered model use | already handled: `claude-opus-5` for security and correctness, `claude-sonnet-5` for tests, `claude-haiku-4-5` for docs |

Genesis Kit itself is a Node.js framework for driving agents over a repository,
not a library this service would depend on; adopting it wholesale is not the
move. Its diagnosis is the useful part, and on the one point where it is
sharpest — that the checker should never see the builder's reasoning — this
project still fails. The obvious experiment is to have the label audit done by a
model that sees the diff and the finding but not the note, and compare against
the three the audit caught by hand.

### The blind label audit — my audit caught two of five

Genesis Kit's sharpest claim is that the checker must not see the builder's
reasoning. The hand audit of the label notes violated that in the obvious way:
the same agent wrote the notes' verdicts and then checked them, knowing it was
looking for overstatement. It reported three. There was no way to tell whether
three was the real number or the number that agent happened to notice.

`scripts/blind_label_audit.py` runs the check properly. For each label it sends a
model the case — title, summary, unchanged context files, diff — and the note,
and nothing else: no verdict, no hint that anything is wrong, no count to find.
Its only job is to decide whether the material supports every factual claim the
note makes. It judged the notes **as originally written**, at `b2991ad`, before
the hand corrections.

**It flagged 5 of 22. The hand audit had found 3, and only 2 of those overlap.**

| note | hand audit | blind audit |
|---|---|---|
| `cve-jupyter-referer-token-log` api_contract | caught | caught |
| `sec-removed-authz-check` | caught | caught, different reasoning |
| `sec-workflow-pull-request-target` | **missed** | caught |
| `intro-urllib3-util-refactor` | **missed** | caught |
| `tst-deleted-test-with-fix` | **missed** | caught |
| `sec-timing-unsafe-compare` | caught | SUPPORTED |

The one it missed is not a failure of it: the note there is factually accurate,
and the hand objection was that it describes the *replacement* rather than the
code under review. That is relevance, not accuracy, and accuracy was all this
checker was asked about.

The three it caught are real, and one is a plain factual error rather than an
overstatement. On `sec-workflow-pull-request-target` the note argued the trigger
change "is not needed for the stated goal" because `pull_request` already fires
for forked PRs. But the same diff adds `NPM_TOKEN: ${{ secrets.NPM_TOKEN }}`, and
`pull_request` does **not** expose repository secrets to a fork's pull request.
The change is not redundant; it is the precise reason the swap is dangerous. Two
agents wrote and reviewed that note and neither looked at the `env:` block three
lines below.

All three notes are corrected.

#### What the experiment cost to get right

The first run flagged 7, and two of those were artifacts of the harness rather
than faults in the notes: the prompt sent only the diff, while the panel also
receives the pull request title, the summary and the unchanged context files.
Blind to those, the checker correctly said that `execute()`'s documented params
contract was not visible — it is, in `billing/db.py`, which the case supplies and
the prompt withheld. Giving the checker what the panel gets dropped the count
from 7 to 5.

The lesson generalises past this script: an independent checker starved of
context does not fail safely, it fails *loudly*, and its extra findings look
exactly like diligence.

---

## Retrieval, measured for the first time

Every other component had a number and retrieval never did. It now does, and it
is the weakest thing in the system.

### What is measured, and why this ground truth

"Relevant context" is a judgement, and a relevance set chosen by whoever also
reads the results is worth about as much as a negative control written by whoever
writes the labels — which this repository has been burned by four times. So the
ground truth is mechanical: **for a diff, take the identifiers the added lines
call but do not define, and find where the repository defines them.** Those
definitions are what a reviewer has to look up, they are derivable by grep rather
than by opinion, and supplying them is the entire point of repository retrieval.

Commits are sampled by taking every Nth Python-touching commit in each
repository's history, so which diffs are measured cannot be steered either.
`scripts/eval_retrieval.py` does both. It costs nothing to run: local embedder,
local database, no API calls.

### The result

| repo | cases | recall@12 | MRR | precision | definitions needed / case |
|---|---|---|---|---|---|
| tornadoweb/tornado | 5 | 0.296 | 0.344 | 0.092 | 7.0 |
| urllib3/urllib3 | 6 | 0.517 | 0.210 | 0.115 | 3.3 |
| psf/requests | 5 | 0.342 | 0.164 | 0.172 | 5.0 |
| scrapy/scrapy | 5 | 0.158 | 0.142 | 0.062 | 18.8 |
| **all** | **21** | **0.337** | **0.215** | **0.110** | 8.3 |

**Retrieval surfaces about a third of the definitions a diff calls, and the first
useful result sits around rank 5 of 12.** Against the reviewer's numbers — recall
1.000, precision 0.982 — this is not in the same class.

### Before this is quoted anywhere

- **The vector half is not semantic.** No OpenAI key is configured, so
  `get_embedder()` returns `HashingEmbedder`, which the module's own docstring
  describes as capturing "lexical overlap, not meaning". Hybrid search is
  advertised as vector kNN fused with full-text; as actually deployed here it is
  two lexical signals fused with each other, and reciprocal-rank fusion of two
  correlated signals buys much less than fusion of two independent ones. This is
  the largest confound in the table and the obvious next experiment: the same
  script against `text-embedding-3-small` needs only a key.
- **Precision is bounded low by construction** and should not be read as a
  quality signal: `top_k` is 12 per changed file while a typical diff needs ~8
  definitions, so the ceiling is low whatever the ranking does.
- **Large diffs are treated harshly.** Scrapy's worst case needs 58 definitions
  and can retrieve at most 12; scoring recall over that is close to meaningless,
  and scrapy's 0.158 is mostly that effect rather than worse retrieval.
- **Roughly half the sampled commits were skipped** because no called identifier
  resolved to a definition outside the changed files. The measured cases are
  therefore the ones where retrieval had something to find, which is the right
  population but a smaller one than the sample suggests.

### What this changes

The honest summary of the project shifts. The panel and the gate are measured and
good; retrieval is measured and poor, and it feeds the panel. That the reviewer
scores as well as it does while receiving a third of the context it asks for
suggests the agents are leaning on the diff far more than on retrieved context —
which is worth knowing, and is testable by running the eval with retrieval
disabled entirely and seeing whether any number moves.

### The ablation: the eval cannot see retrieval at all

Before running it, a discovery that matters more than the result. `run_case`
builds its `ReviewContext` directly from each case's hand-authored
`context_files`. **The eval has never called `build_context` and has never
exercised retrieval.** Twenty of the 63 cases supply context; all of it was
written by hand to be exactly relevant. Every panel number in this file was
therefore measured under an assumption of perfect retrieval, while production
runs on retrieval that recalls 0.337.

The ablation — `--no-context`, the same 20 cases, three runs each, $4.26:

| | with context (hand-authored) | no context |
|---|---|---|
| recall | 1.000 *(spread 0.000)* | 1.000 *(spread 0.000)* |
| labels missed | 0.0 | 0.0 |
| unlabelled / run | 0.0 | 1.0 |
| false positives | 0.0 | 0.0 |
| cost | $1.43 | $1.42 |

**Nothing moved.** Not one label was lost. Per case, every one of the twenty
scores identically in both arms — including `ctx-changed-default-breaks-caller`
and `ctx-duplicate-index-migration`, the two cases written specifically to
*require* repository context to solve.

That last part is the finding. This is not evidence that retrieval is worthless;
it is evidence that **the eval cannot measure whether retrieval is worth
anything**, because even the cases designed to depend on context do not depend on
it. The diff alone is sufficient everywhere, so removing context costs nothing
and adding perfect context buys nothing. Two cases were built to close exactly
this gap and neither does.

So the state of knowledge on retrieval is: it recalls a third of what a diff
calls for, the vector half of its "hybrid" search is a hashing embedder rather
than a semantic one, and this eval is structurally incapable of telling anyone
whether either of those facts costs a review anything.

Fixing that means cases whose defect is genuinely invisible in the diff — where
the changed code is correct on its face and only wrong against a contract, a
caller or an invariant defined in a file the diff does not touch. Writing one
that survives is harder than it sounds: the four negative controls in this file
all failed on the first attempt, and a context-dependent case has the same
failure mode in reverse.

### Rebuilding the context-dependent cases — one fixed, one not

Two problems, found in order.

#### The case summary was being fed to the panel as the pull request body

`fixtures.py` set `body=self.summary`, and the agent prompt renders `pr.body`.
The summaries are documentation for whoever reads `cases.py` and **14 of 63 of
them describe the defect** — `ctx-changed-default-breaks-caller` said "the
retrieved caller relies on the old value", and `intro-tornado-cookies-move` said
"the defective line is in httpserver.py, the code it breaks is in web.py". Every
run in this file was scored with those descriptions in the prompt.

`summary` no longer reaches the model. Cases may now set `body` explicitly, for
what an author would actually write; it defaults to empty, which the prompt
renders as "(no description)".

**Every recorded baseline predates this fix and was measured with the leak.** The
numbers above are not comparable to anything measured from here on, and recall in
particular should be expected to fall.

#### One case is now genuinely context-dependent

`ctx-changed-default-breaks-caller` is rebuilt as a purely additive call site
passing `timeout=30` to a helper whose docstring — in an unchanged context file —
says the unit is milliseconds. Nothing in the diff contradicts it.

| | with context | no context |
|---|---|---|
| `ctx-changed-default-breaks-caller` | 1/1, 1/1, 1/1 | **0/1** |

Found every run with context, missed without it. That is the first case in this
set that demonstrably needs retrieval. The no-context arm is a **single run** —
credits ran out during run 2 — so it wants confirming at three.

Its first rebuild failed, and instructively: I had included a sibling call with
`timeout=45_000` for realism, and the `before` used `timeout=30_000`, so the diff
showed `30_000 → 30` outright. The panel reported "timeout unit inconsistent: 30
vs 45_000" and never needed the docstring. **The case leaked its own answer
through the very detail added to make it look real.**

#### The other case still does not need context, and shows why this is hard

`ctx-duplicate-index-migration` is found without context every time — but never
for the right reason. Twice now the panel has commented on the labelled line
without noticing the duplication at all:

- "CREATE INDEX without CONCURRENTLY blocks writes to invoices"
- after adding `CONCURRENTLY`: "CREATE INDEX CONCURRENTLY cannot run in a
  transactional migration" — ignoring the `-- transactional: false` comment
  directly above it

Both are true, neither is the defect, and both score as hits because matching is
location plus concern *family*, and `error_handling` and `logic` are both
correctness. Family matching was a deliberate choice — one defect genuinely has
several fair readings — and here it is credit for the wrong observation.

The general lesson: **a context-dependent case needs a labelled line that is not
itself interesting.** `CREATE INDEX` is a magnet for true generic commentary, so
any defect anchored to one will be "found" whatever the panel actually noticed.
The fix is either a defect on a boring line, or a matcher that demands the
labelled concern rather than its family — and the second would undo an earlier
fix for good reasons, so the first is the one to try.

## Re-baseline with the body leak closed — recall finally moved

$15.19, three runs, 63 cases. The first measurement in this file taken without
the case summary in the prompt.

| metric | leak present | leak closed |
|---|---|---|
| recall | 1.000 *(spread 0.000)* | **0.977** *(0.971–0.986)* |
| precision, strict | 0.982 | 0.899 *(0.889–0.919)* |
| precision, lenient | 1.000 | 0.982 *(0.973–0.986)* |
| calibration error | 0.091 | **0.079** |
| agent attribution | 0.946 | 0.958 |
| gate decision match | 1.000, 1.000, 1.000 | **0.900, 0.900, 0.850** *(mean 0.883)* |
| false positives / run | 0 | 2 |
| unlabelled / run | 2, 2, 0 | 8, 5, 7 |

**Recall dropped below 1.000 for the first time, with a real spread.** After many
runs pinned at a ceiling, the set can now report a regression.

### The prediction was right and the reasoning was wrong

I predicted 0.96–1.000, and 0.977 is inside it. That is the first correct
prediction in this file. The stated reason was wrong, which matters more.

The hypothesis was that the 14 summaries describing their own defect were
propping recall up. **None of the three cases that lost labels had a leaking
summary** — `doc-misleading-name` (3 of 3 runs), `tst-repeated-coverage-gap` and
`sec-weak-password-hash` (1 each). The defect-describing summaries were removed
and cost nothing measurable.

What actually changed is larger than the leak. Every case previously carried
*some* body, because every case had a summary. Now every case carries none, and
the prompt renders "(no description)". So the panel did not merely lose 14 hints;
it lost the author's stated intent on all 63. `doc-misleading-name` failing all
three runs fits that reading — deciding whether `get_tenant_fresh` is misleadingly
named is a judgement about what the change claims to do, and there is no longer a
claim.

### The gate regression is the more interesting result

`gate decision match` fell from a flat 1.000 to 0.900, 0.900, 0.850 — mean 0.883. (An earlier version of this section quoted the 0.850 alone, which is the last run's figure and not the result; the report prints the final run while the stability table omits this metric, and I read the headline instead of the distribution — the same error the per-run guard was added to catch.) On the last run all three disagreements are
the same shape — a `neg-*` case expected to be suppressed and escalated instead:

```
  neg-allowlisted-dynamic-sql      expected suppress  got escalate  (0.60)
  neg-dependency-bump              expected suppress  got escalate  (0.55)
  neg-typescript-type-narrowing    expected suppress  got escalate  (0.58)
```

Both new false positives are on the same kind of case: a routine dependency bump
read as a breaking change, and MD5-over-template-source read as cache poisoning.
All five confidences sit between 0.55 and 0.60 — the panel is not confident, it is
*unanchored*. Stripped of any statement of intent, a benign change looks like an
unexplained one, and an unexplained change gets escalated.

That is a finding about the input, not the reviewer. A real pull request usually
has a description; "(no description)" for all 63 cases is not realism, it is the
opposite error from the leak. The fix is neither the summary nor emptiness but a
written `body` per case — what an author would plausibly say, without narrating
the defect. Two cases have one already.

### What is comparable to what

Nothing above this section shares a prompt with anything below it. The leak
closed and the labels changed in the same window, so the old strict-precision
figures are not comparable either. This run is the new zero.

## Author bodies for all 63 cases

Every case now carries a written `body` — author-plausible intent, no defect
narration. The two that already had one (`ctx-duplicate-index-migration`,
`ctx-changed-default-breaks-caller`) are unchanged. A unit guard refuses empty
bodies and refuses `body == summary`.

This closes the "(no description)" unanchoring. It does **not** replace the
nobodyleak baseline: the prompt changed again, so headline numbers above are
still the wrong zero for anything measured with bodies.

## Partial re-baseline with bodies — credits died on run 2

Attempted `--repeat 3`. Anthropic credit balance hit zero mid-run 2; the outage
guard discarded the incomplete run and kept run 1 (`baseline-63-withbodies.json`,
~$5.21). **Not a three-run zero.** Directional only:

| metric | nobodyleak (3-run mean / last) | with bodies (1 run) |
|---|---|---|
| recall | 0.977 *(0.971–0.986)* / 0.973 | 0.973 |
| precision, strict | 0.899 / 0.889 | **0.920** |
| precision, lenient | 0.982 / 0.973 | **0.986** |
| calibration error | 0.079 / 0.072 | 0.071 |
| gate decision match | 0.850 (last) · 0.900, 0.900, 0.850 | **0.900** |
| false positives | 2 (last) | **1** |
| unlabelled | 8, 5, 7 | **5** |

Gate disagreements on the kept run:

```
  neg-allowlisted-dynamic-sql      expected suppress  got escalate   (0.65)
  neg-typescript-type-narrowing    expected suppress  got auto_post  (0.74)
```

`neg-dependency-bump` no longer escalates — that was one of the three empty-body
escalations. `neg-typescript-type-narrowing` still mismatches, but moved from
escalate@0.58 to auto_post@0.74: the body ("Narrows the event union… Types only.")
is being read, and the panel is now arguing with it rather than inventing severity
from silence.

Misses on the kept run: `doc-misleading-name` (still), and `sec-command-injection`
as a correctness/logic reading of unquoted interpolation (the security label is
elsewhere — family matching may still score the injection finding as a hit; this
miss is the secondary label).

**Needs a full three-run re-baseline once credits are topped up** (~$15). Until
then, do not treat `baseline-63-withbodies.json` as the comparable zero.

### Auditing the written bodies, and what the single run does not show

All 63 cases now carry an author-plausible `body`. Audited rather than assumed,
because an LLM-written body narrating the defect would reinstate the exact leak
that was just closed:

- **Term overlap between each body and its own `EXPECT` note**, which is what
  leakage looks like mechanically. Median **0.100**. The highest, 0.43, is
  `cor-contract-break-return-shape`, whose body says the return type went from a
  tuple to a dict — which the diff shows anyway. Nothing narrates a consequence.
- **The bodies that read closest to the defect are the realistic ones.**
  `sec-removed-authz-check` says "dropping the per-object ownership loop —
  callers are already authenticated", which is the authn/authz conflation a
  reviewer is supposed to catch, in the author's own voice. That is what a bad
  pull request actually looks like. `sec-jwt-unverified` and
  `sec-verbose-error-leak` do the same: they state an unwise intent rather than
  admit a bug.

The bodies are sound. **The single run that followed does not demonstrate
anything.**

| metric | no body (3 runs) | with bodies (1 run) |
|---|---|---|
| recall | 0.977 [0.973–0.986] | 0.973 |
| precision, strict | 0.899 [0.889–0.919] | 0.920 |
| precision, lenient | 0.982 [0.973–0.986] | 0.986 |
| false positives | 1.3 [1–2] | 1 |
| unlabelled | 6.7 [5–8] | 5 |
| gate decision match | 0.883 [0.850–0.900] | 0.900 |
| category agreement | 0.915 [0.889–0.944] | 0.913 |
| agent attribution | 0.972 [0.958–0.985] | 0.957 |

**Every figure lands inside the no-body spread.** Strict precision is a hair above
(0.920 against a 0.919 maximum) and attribution a hair below; neither is a result.
The gate's 0.900 is exactly the best of the three no-body runs, not an improvement
on them.

So the "restoring intent recovers the gate" story is unsupported. It remains the
best available explanation of why the gate fell when bodies were removed — that
part is real, 1.000 flat versus 0.883 — but one run at the top of the prior range
is what noise looks like. Two more runs settle it; nothing here should be quoted
until they exist.

## Three-run with-bodies baseline — gate settled

Runs 2–3 completed after a credit top-up (`baseline-63-withbodies-runs23.json`),
combined with run 1 into `baseline-63-withbodies-3.json`. Per-run unlabelled
counts: **5, 5, 4**.

| metric | no body (3 runs) | with bodies (3 runs) |
|---|---|---|
| recall | 0.977 [0.973–0.986] | **0.986** [0.973–1.000] |
| precision, strict | 0.899 [0.889–0.919] | **0.925** [0.920–0.934] |
| precision, lenient | 0.982 [0.973–0.986] | 0.986 [0.986–0.986] |
| false positives | 1.3 [1–2] | **1, 1, 1** |
| unlabelled | 6.7 [5–8] | **4.7** [4–5] |
| gate decision match | 0.883 [0.850–0.900] | **0.900, 0.900, 0.900** |

**Did the gate improve?** Mean yes — 0.883 → 0.900 — and the distribution
tightened: the 0.850 dip is gone. It did **not** rise above the prior ceiling
(still 0.900), so this is recovery of consistency, not a new high. The same two
disagreements appear on every run:

```
  neg-allowlisted-dynamic-sql      expected suppress  got escalate
  neg-typescript-type-narrowing    expected suppress  got auto_post
```

`neg-dependency-bump` stays suppressed across all three — that was the
empty-body escalation that actually moved. The remaining two are stable
disputes with the authored intent, not unanchored noise.

This three-run file is the comparable zero for anything measured with bodies.

### Unrelated change bundled in

`tests/conftest.py` switched its environment setup from `setdefault` to forced
assignment, so a developer with a sourced `.env` no longer has a real
`GITHUB_WEBHOOK_SECRET` leak into the webhook tests and 401 every happy path.
That is a correct fix and it has nothing to do with pull request bodies; it is
noted here so it is not mistaken later for part of this change.

### With bodies, at three runs — one thing is demonstrated, the gate is not

$10.51 for the two remaining runs; fixtures unchanged since the first, so the
three combine into one distribution (`baseline-63-withbodies.json` plus
`baseline-63-withbodies-runs23.json`).

| metric | no body (3 runs) | with bodies (3 runs) |
|---|---|---|
| precision, strict | 0.899 [0.889–0.919] | **0.925 [0.920–0.934]** |
| unlabelled | 6.7 [5–8] | 4.7 [4–5] |
| recall | 0.977 [0.973–0.986] | 0.986 [0.973–1.000] |
| precision, lenient | 0.982 [0.973–0.986] | 0.986 [0.986–0.986] |
| false positives | 1.3 [1–2] | 1.0 [1–1] |
| gate decision match | 0.883 [0.850–0.900] | 0.900 [0.900–0.900] |
| category agreement | 0.915 [0.889–0.944] | 0.924 [0.913–0.943] |
| agent attribution | 0.972 [0.958–0.985] | 0.962 [0.957–0.972] |

**Strict precision separates cleanly**: 0.920–0.934 against 0.889–0.919, no
overlap. Giving the panel a plausible statement of intent makes it say fewer
things nobody has agreed are worth saying — unlabelled findings fall from 6.7 to
4.7 per run, which is the same fact seen from the other side. That is the one
result here that survives its own error bars.

**The gate is not the result I claimed it would be.** It is flat at 0.900 across
all three runs, against 0.883 [0.850–0.900] without bodies. The ranges overlap:
0.900 is the top of the no-body range and every no-body run but one already hit
it. What bodies demonstrably remove is the *variance* — the 0.850 run does not
recur — and with n=3 on each side, one excursion out of three is not enough to
call that real either.

More to the point: **the gate does not come back.** It was a flat 1.000 when the
summaries were leaking, and restoring author intent moves it to 0.900, not 1.000.
So the perfect gate score was the leak, not the presence of a description. My
"unanchored escalation" story explained part of something real — the panel does
say less, more precisely, when told what a change is for — and it does not
explain the gate, which I had presented as its main evidence.

Recall, lenient precision, false positives, category agreement and attribution
all overlap. Nothing there is demonstrated in either direction.

**This three-run distribution is the reference baseline.** It is the first one
measured with a prompt that is neither leaking the answer nor withholding the
description a real pull request would carry.

## Investigating the gate — two broken cases and one backwards rule

The gate sat at a flat 0.900 across three runs. Twenty cases carry an
`expected_decision`, so that is exactly two disagreements, and they were the same
two every run:

```
  neg-allowlisted-dynamic-sql     expected suppress  got escalate    conf 0.65, 0.60, 0.55
  neg-typescript-type-narrowing   expected suppress  got auto_post   conf 0.74, 0.88, 0.80
```

(An earlier note here said three cases were escalating. It was two, and only one
of them escalates — the other auto-posts.)

### The old 1.000 was the leak, and this is the proof

Four case summaries — the pull request body until two commits ago — stated the
verdict outright rather than describing the change:

```
  neg-allowlisted-dynamic-sql     "...and correct, because the only interpolated values are literals..."
  neg-clean-extract-method        "Any finding here is a false positive..."
  neg-typescript-type-narrowing   "Any finding here is a false positive."
  trap-subprocess-list-args       "...and is in fact the fix for one."
```

Three of the four are `suppress` negative controls. The panel was being told "any
finding here is a false positive" on the precise cases testing whether it would
stay quiet. A flat gate of 1.000 under those conditions measured obedience. The
drop to 0.900 is not a regression; it is the first honest reading.

### Both remaining disagreements are the cases, not the gate

**`neg-typescript-type-narrowing`** was labelled a types-only refactor and was
not one. It changed `` `click at ${x}` `` to `` `click at ${x}, ${y}` `` — a real
output change, which the panel reported at 0.88 confidence on every run, citing
the body's "types only" claim. Sixth hand-written negative control found to
contain a real defect. The `, ${y}` is removed; the diff now emits byte-identical
strings and only the casts are gone.

**`neg-allowlisted-dynamic-sql`** guards both `sort` and `direction` and tested
only `sort`. "No test exercises rejection of an invalid `direction`" was simply
true. The missing test is added, so the observation now has nothing to land on.

Worth stating plainly: the gate metric was not measuring the gate. It was
reporting two defective fixtures, and both defects were things the panel saw and
I had not.

### The rule that was genuinely backwards

Rearranging the precedence does **not** fix either case — I checked by
recomputing every recorded decision offline under four candidate orderings, which
costs nothing and reproduces the live gate exactly. `neg-allowlisted-dynamic-sql`
merely moves from `escalate` to `auto_post`; no defensible gate stays silent
about a finding the set's own `PERMITTED_CONCERNS` policy declares legitimate.

But the ordering was wrong anyway, and measurement is how it surfaced. "Nothing
cleared the posting bars" was tested *after* "overall confidence is low", so:

- a **confident** trivial finding suppressed, and
- an **uncertain** trivial finding escalated.

Low overall confidence is the normal state of a review that found only weak
signals, so the effect was to spend a human on the pull requests with least to
say — the exact noise failure this gate exists to prevent. Escalation routes
findings to a person; with nothing that clears the bars there is nothing to
route. Suppress is now checked first.

This is adopted on the argument, not the number: across three recorded runs it
changes one decision in one run. It is not demonstrated by the eval and is not
claimed to be.

### Verifying the two repaired controls — four rounds, all of them earned

Both now suppress on every run. `neg-allowlisted-dynamic-sql` fixed in one round:
with the missing `direction` test added it produces **zero findings**, confidence
1.00, three runs out of three.

`neg-typescript-type-narrowing` took four, and the panel was right every time —
including about code I wrote to fix it.

| round | what I changed | what the panel said |
|---|---|---|
| 1 | removed `` `, ${y}` `` from the click output | *"switch without default can return undefined at runtime"* — true; the old `if`/`return` always returned, an exhaustive switch does not |
| 2 | added `const unreachable: never = event; return unreachable;` | *"exhaustiveness default returns the event object, not a string"* — **true, 3 of 3 runs.** Returning `never` is the known anti-pattern: at runtime it returns the value and violates the declared return type |
| 3 | made the default `throw` | *"new default branch throws where the old code returned a string"* — true; the body still claimed "types only" |
| 4 | reduced the diff to removing the two casts, nothing else | *"casts removed but the discriminated-union change is not in the diff"* — true; the body claimed a union change the diff did not contain |

Round 4 also corrected the body, which by then was the only thing left that was
false. The diff is now two lines — the casts — and the body says what those two
lines do.

Round 2 is the one worth sitting with. **I introduced a real defect while
repairing a negative control, and the reviewer caught it on every run at
0.58–0.62.** Not a lucky single run; three for three, with the mechanism named
correctly.

Round 3 is worth sitting with for the opposite reason. It *passed* — suppress on
all three runs — while the panel was still reporting a true mismatch between the
body and the diff, at 0.55–0.58. It passed because those findings fall below the
posting bar, not because there was nothing to find. A control that clears the bar
by a hair is not the same as a control that is clean, and only reading the
findings distinguishes them. The gate number alone would have called round 3 done.

Eight negative controls in this file have now been found to contain something
real. The pattern is no longer worth treating as a surprise: a case written to be
unremarkable, by the person who also writes the labels, is unremarkable only
until someone looks.

## Re-baseline after the fixture and gate fixes — and a guard that missed

One clean run and one that must not be read. Credits ran out during run 3, the
retention fix kept what had completed, and **run 2 was already degraded when it
was kept**. Unlabelled per run: 8, 2.

| | run 1 (clean) | run 2 (degraded — do not read) |
|---|---|---|
| recall | **0.986** | 0.726 |
| gate decision match | **1.000** | 0.950 |
| failed agent calls | none | 46 of 252 |
| tests agent recall | — | 0.00 |
| cost | $5.38 | $4.07 |

Run 2's "tests agent recall 0.00" was eleven failed calls, not a specialist with
nothing to say. Its recall of 0.726 is the sound of a provider running out of
money.

### The guard that should have caught it

`_refuse_if_everything_failed` only counted cases that lost their **whole** panel.
Run 2 lost 46 of 252 individual agent calls spread across 63 cases without any
single case losing all four, so it passed — and because the report renders the
last run, that degraded run became the headline. This is the exact failure the
guard was written for, stated in its own docstring: *a partial outage is more
dangerous than a total one, because it looks like a measurement.* It was guarding
one shape of partial and not the other.

There is now a second threshold: more than 10% of individual agent calls failing
refuses the run, whatever the distribution. Whole-panel loss is still checked
first, because it is the more specific diagnosis and deserves its own message.
Both are tested, including that a handful of stray failures still scores — a
degraded result below the threshold is still a result.

### What run 1 actually says

| metric | before (3 runs) | run 1, after |
|---|---|---|
| recall | 0.986 [0.973–1.000] | 0.986 |
| **gate decision match** | 0.900 [0.900–0.900] | **1.000** |
| precision, lenient | 0.986 [0.986–0.986] | 0.986 |
| precision, strict | 0.925 [0.920–0.934] | 0.890 |
| category agreement | 0.924 [0.913–0.943] | 0.918 |
| agent attribution | 0.962 [0.957–0.972] | 0.945 |

**The gate reaches 1.000**, as predicted — though that prediction was close to a
tautology: the two cases that were failing were the only two failing, both were
repaired, and both were verified to suppress three times before this run started.
It confirms the repairs took; it is not independent evidence of anything.

Strict precision fell to 0.890, below the previous range, with unlabelled findings
at 8 against 4.7. That is the one movement worth attention and **a single run
cannot establish it** — the previous distribution spans 0.920–0.934 and one
observation outside a range is what noise looks like from the inside. Two more
clean runs would settle whether repairing the fixtures cost precision or whether
this is the ordinary spread of a set that produces a handful of unlabelled
findings per run.

Nothing here is a baseline yet. One clean run is a reading, not a distribution.

## Two more clean runs — the precision drop is inside the spread

`baseline-63-gatefix-runs23.json`. Both runs finished with every agent call
returning. Unlabelled per run: 8, 4. Cost $5.29 and $5.27.

Together with the clean run above, that is three readings of the same
configuration:

| | clean 1 | clean 2 | clean 3 |
|---|---|---|---|
| recall | 0.986 | 0.986 | 0.986 |
| precision, strict | 0.890 | 0.896 | 0.937 |
| precision, lenient | 0.986 | 1.000 | 0.987 |
| gate decision match | 1.000 | 1.000 | 0.950 |
| false positives | 1 | 0 | 1 |
| unlabelled | 8 | 8 | 4 |
| failed agent calls | 0 | 0 | 0 |

Strict precision across the three is 0.890–0.937, mean 0.908. The earlier
three-run band was 0.920–0.934. The ranges overlap, and the gap between the
means (0.017) is smaller than the spread of these three runs (0.047). Repairing
the fixtures did not move strict precision by an amount this set can show.

Recall is the same number on every clean run, and the same case is the miss
each time: `doc-misleading-name`.

The gate is 1.000 on two runs and 0.950 on the third. The single disagreement
is `sec-terraform-public-bucket`: the invoices bucket was found, confidence
0.99, and the review auto-posted where the label says escalate. The two earlier
clean runs escalated it. One run in three, on a finding the panel did not miss.

## `doc-misleading-name` — three prompt attempts, and what it actually is

I described this as "never caught, a standing capability gap". **That was wrong**,
and reading the recorded runs rather than the summary of them says so: across 22
recorded runs the docs agent names `get_tenant_fresh` in **9 of them**, as
`readability`, `documentation` or `api_contract`. It is intermittent at roughly
40%, not absent.

Two separate things were going on.

**A scoring loss, now fixed.** In one of the three clean gatefix runs the docs
agent *did* name it, filed as `api_contract`, and it scored as a miss because the
label is `docs/readability` and the merge had left the docs specialist visible
only in `agreeing`. Rescoring that run under the current matcher turns it into a
hit (`missed` 1 → 0). That fix is narrow on purpose: an `api_contract` finding
satisfies a docs label only when docs is among the contributors, so a
correctness-only contract finding — a genuinely broken signature — still cannot.

**A prompt inconsistency, fixed, and it did not help.** `docs.md` defines three
kinds of finding: stale documentation, an undocumented public contract, and "a
name that actively misleads: a `get_*` that mutates" — which is this case,
verbatim. But the per-run `focus()` line offered an exit test naming only the
first two: *"if nothing is now untrue and no public contract is undocumented,
return zero findings."* A misleading name satisfies both, so the model was being
told to stay silent, correctly, by the narrower of two instructions it had been
given. That is why two earlier attempts to state the rule more firmly failed:
emphasis was never the problem.

The exit test now names all three. Measured over three runs on the case plus every
negative control: the case was found **1 of 3**, against a prior rate of about 4 in
10 — no improvement that three runs can see. No noise was added; the one negative
control that escalated did so on a `tests` coverage finding at 0.6 that has
hovered at the threshold on that case for several rounds, with no docs finding
involved.

The change is kept anyway, on the same standard applied to the gate reorder: the
two instructions genuinely contradicted each other, and an exit test that omits a
category its own system prompt defines is wrong whether or not fixing it moves a
number. It is recorded here as not having moved one.

**Three attempts have now failed, so this is recorded as a measured limit rather
than fought further.** The label stays `EXPECT`. The cache wipe on line 11 is
caught every run; the misleading name on line 8 is caught about four times in ten.
Recall of 0.986 — one label of 73, the same one each time — is the honest ceiling
of this panel on this set, and moving the label to make the number go away would
only cost us the one place the set still says something uncomfortable.

## A real embedder — and it was not the recall bottleneck

Retrieval's "hybrid vector kNN + full-text search" was, as deployed, **two lexical
signals**: with no OpenAI key, `get_embedder()` returned the feature-hashing
embedder, and reciprocal-rank fusion over two correlated signals buys much less
than over independent ones. The fix needed no vendor: a trained static embedding
model (`minishlab/potion-base-8M`, 256-dimensional, ~30MB, CPU, offline after one
download) is now a third provider, `EMBEDDING_PROVIDER=local`.

It is genuinely semantic in a way hashing cannot be — a function against a prose
description of itself scores 0.749, against unrelated code 0.155.

### Measured, same 22 diffs, both arms

| repo | n | recall@12 hash → local | MRR hash → local |
|---|---|---|---|
| tornadoweb/tornado | 6 | 0.397 → 0.313 | 0.339 → 0.417 |
| urllib3/urllib3 | 6 | 0.517 → 0.450 | 0.208 → 0.140 |
| psf/requests | 5 | 0.342 → **0.602** | 0.165 → **0.600** |
| scrapy/scrapy | 5 | 0.144 → 0.147 | 0.137 → 0.260 |
| **all** | **22** | **0.360 → 0.378** *(+0.019)* | **0.218 → 0.347** *(+0.130)* |

**Recall barely moved; ranking improved substantially.** The first useful result
goes from about rank 4.6 to rank 2.9 — a 60% relative gain in MRR — while the set
of definitions retrieved at all is essentially unchanged, and is *worse* on two
repositories out of four.

So the prediction implied by "two lexical signals" was wrong. A semantic embedder
does not find more of the definitions a diff calls; it orders the ones it finds
much better. **Whatever caps recall at ~0.37 is not the embedder** — the
candidates are chosen before ranking, by chunk boundaries, the `exclude_paths`
rule and the per-file `top_k` split, and that is where the next look belongs.

Unlike every other measurement in this file, this one has **no run-to-run noise**:
both embedders are deterministic, so the numbers are exact for these 22 cases. The
uncertainty is entirely in whether 22 cases across four repositories generalise,
not in the measurement.

### Not made the default

`hash` stays the default and CI never downloads a model. Recall did not improve,
the dependency is optional (`pip install 'pr-sentinel[local-embeddings]'`), and a
better ranking is worth having where retrieval is actually used but is not worth
making the service heavier by default on this evidence.

The column contract is handled by padding, which is exact rather than
approximate: appending zeros changes neither a dot product nor a norm, so cosine
ranking on a 256-dimensional vector padded to `vector(1536)` is identical to
ranking on the unpadded one. It wastes storage in proportion to the gap, which a
migration narrowing the column would recover. Truncation is refused outright,
because dropping components does change the ranking.

### A reproducibility bug this exposed, in my own harness

The first comparison was invalid and nearly reported. `scripts/eval_retrieval.py`
sampled commits from the clone's current HEAD, and `measure()` leaves the clone
checked out at whichever commit it last examined — so the second arm sampled from
a truncated history and got 14 cases where the first got 21. Two arms of a
comparison silently running on different cases is exactly the shape of a result.
Sampling now reads the remote's default branch explicitly, and both arms above ran
on identical case sets, which is why the per-repo `n` matches.

## What was actually capping retrieval recall

Three candidates were plausible: chunking, the embedder, or the retrieval
settings. Measured over **177 needed definitions**, asking of each miss whether
the definition was never a candidate or was a candidate that ranked too low:

| | |
|---|---|
| never indexed at all | **0 (0.0%)** |
| retrieved in the top 12 | 23.2% |
| ranked 13–50 | **31.6%** |
| ranked below 50 | 45.2% |
| cut by the per-file `top_k` split | 7.9% |

**Chunking is not the problem** — every needed file had chunks in the index. And
the embedder was not either: a semantic model moved recall by +0.019 while moving
MRR by +0.130. Nearly a third of the misses were findable and sitting just outside
the window.

### The window was smaller than the budget that pays for it

`render_repository_context` caps the prompt at 24,000 characters. At a mean 1,658
characters per chunk that holds about **15 chunks** — but `retrieval_top_k` was
**12**, so only ~20k of the 24k was ever used. The system was paying for context
it did not fetch.

`top_k` is now 20, deliberately above what the budget holds, so the budget is the
binding constraint and is never under-filled. Anything beyond it is discarded at
render time, so the extra candidates cost ranking work and **no tokens**.

| arm | recall | MRR |
|---|---|---|
| hash, top_k 12 | 0.360 | 0.218 |
| **hash, top_k 20** | **0.459** | 0.213 |
| local, top_k 12 | 0.378 | 0.347 |
| local, top_k 20 | 0.423 | 0.365 |

**Recall 0.360 → 0.459, a 27% relative gain for nothing.** Best recall is the
hashing embedder at the wider window; best ranking is still the semantic one. The
default stays `hash`.

### What is left is priced, not free

Beyond filling the existing budget, recall is bought with context tokens:

| prompt budget | chunks that fit | recall |
|---|---|---|
| 24k chars *(default)* | 15.1 | 0.356 |
| 48k | 31.0 | 0.416 |
| 96k | 68.9 | 0.657 |

Reaching 0.657 costs roughly four times the repository context in **every agent of
every review**, four agents deep. That is a real bill against the resource this
system exists to protect, so the budget is now `RETRIEVAL_CONTEXT_CHARS` with the
curve recorded beside it, and the default is unchanged. It is a decision for
whoever is paying, made with numbers instead of a shrug.

### One measurement that surprised me

Global ranking at k=12 scores **worse** than the per-file split at k=12 (0.348
against 0.378). The split looks like a premature truncation and is partly doing
something useful: guaranteeing every changed file contributes candidates, rather
than letting one file's neighbourhood monopolise a small window. It is kept.

## Retraction: every retrieval number before this was measured on a broken query

Chasing the "ranking problem" — 45% of needed definitions ranking below 50 — found
the cause, and it was in my measurement harness, not the system.

`scripts/eval_retrieval.py` built its `DiffFile` objects as
`DiffFile(path=..., patch=...)` and never parsed hunks. **Both halves of the
retrieval query walk `DiffFile.hunks`**, so with none the query was the file path
and nothing else:

```
fts_query -> 'tornado or http1connection or httpclient or simple'
```

The content side of the query — the identifiers in the added lines, which is the
entire point — was absent from every retrieval measurement ever recorded here. The
tell was a number too clean to be real: **0 of 104** identifiers whose definitions
were needed appeared in the full-text query. Not a low rate. Zero. A heuristic
that bad does not happen; a missing input does.

Production was never affected. It builds `DiffFile` through `build_diff_file`,
which calls `parse_patch`. Only the harness was wrong.

### Re-measured with hunks parsed

| arm | recall | MRR |
|---|---|---|
| hash, top_k 12 | **0.665** | 0.369 |
| hash, top_k 20 | **0.717** | 0.367 |
| local, top_k 12 | 0.676 | **0.528** |
| local, top_k 20 | 0.683 | 0.530 |

*Previously reported, path-only query:* hash@12 recall 0.360, hash@20 recall 0.459.

**Retrieval recall is about 0.72, not 0.34.** Every conclusion drawn from the old
figure is withdrawn:

- *"Retrieval is the weakest component in the system"* — it is not. At 0.717 it is
  comparable to the rest.
- *"The vector half is not semantic, and that is the largest confound"* — the
  embedder choice moves recall by 0.05 and MRR by 0.16. The semantic model buys
  ranking, which still holds, but the framing that hashing was crippling retrieval
  does not.
- *"Chunking is not the cap, the embedder is not the cap, the prompt budget is"* —
  the diagnostic behind that (0% unindexed, 31.6% ranked 13–50, 45.2% below 50)
  was computed on path-only queries and says nothing about the real system.
- *The 24k / 48k / 96k budget curve* — measured the same way. Withdrawn.

The one change that survives is `retrieval_top_k` 12 → 20, which still helps
(0.665 → 0.717) and still costs no tokens, because the prompt budget discards the
surplus. It was adopted for a reason that turned out to be wrong and is kept for a
reason that is measured.

### What this cost, and the rule it earns

Four rounds of work — a semantic embedder, a `top_k` change, a budget setting, a
177-definition diagnostic — were all steered by a broken measurement, and each
produced a confident write-up in this file. The write-ups were internally honest
about noise and about what was and was not demonstrated. None of that helps when
the instrument is wrong.

The tell was available from the first run and I did not look: **a query is an
input, and no measurement of a search system is trustworthy until its query has
been printed once and read.** The same applies to the panel eval, where the prompt
is the input — which is how the body leak was found, by printing what the model
actually received. That lesson was already paid for once here, and applied to
retrieval only after a second bill.

## Baseline after the security-category fix

Three clean runs, zero failed agent calls, $15.47. Unlabelled per run: 3, 3, 5.

| metric | mean | range | previous 3-run |
|---|---|---|---|
| **gate decision match** | **1.000** | [1.000–1.000] | 0.900 |
| recall | 0.982 | [0.973–0.986] | 0.986 |
| precision, strict | 0.939 | [0.923–0.959] | 0.925 |
| precision, lenient | 0.986 | [0.973–1.000] | 0.986 |
| category agreement | 0.944 | [0.931–0.957] | 0.924 |
| agent attribution | 0.958 | [0.944–0.971] | 0.962 |
| false positives / run | 1.0 | [0–2] | 1.0 |

**The gate reaches 1.000 on all three runs with no disagreements**, against a
previous distribution pinned at 0.900. The ranges do not overlap, and this is the
first confirmation on fresh samples rather than a replay: the world-readable
invoices bucket escalates instead of auto-posting, because `authz` was among the
finding's contributors even though `logic` won the merge.

Everything else overlaps the prior distribution and is not demonstrated. Strict
precision at 0.939 against 0.925 looks like a gain and the ranges
[0.923–0.959] and [0.920–0.934] overlap, so it is not one yet.

**This run does not measure retrieval.** `run_case` builds its context from each
case's hand-authored `context_files` and never calls `build_context`. The
retrieval work of the last few rounds — the harness query fix, `top_k`, the
embedder — is invisible here by construction.

### Two misses that read worse than they are

`sec-command-injection` records two misses across three runs and **the injection
is found every run**: all three produce `[security/injection/critical] Command
injection via shell=True`. The case carries two labels, and it is the secondary
correctness reading — the unquoted f-string breaking on spaces and metacharacters
— that appears only in run 1. Counting labels rather than defects is right for
recall and misleading to read without the findings beside it.

`doc-misleading-name` was found 1 of 3, the same ~40% it has always had. The
`focus()` correction did not move it, as recorded when that change was made.

### What still costs lenient precision

`trap-md5-for-cache-key` fires in two runs of three: *"MD5 digest as cache key
allows collision-driven cache poisoning"* at 0.55–0.60. The trap declares that
clean because the digest is over the template's own source and a canonical
encoding of its context — no secret, no adversary, and a collision costs one
re-render. The panel is not wrong about MD5 being MD5; it is wrong about there
being an attacker. Two of three is frequent enough to be worth a look at the
security prompt's threat-model framing, and low-confidence enough that the gate
suppresses it.

## The security prompt learns to name the adversary

`trap-md5-for-cache-key` was firing in two runs of three with *"MD5 digest as
cache key allows collision-driven cache poisoning"*. The panel was right that MD5
is MD5 and wrong that anyone was attacking it: the digest covers the template's
own source and a canonical encoding of its context, nobody supplies a colliding
input, and a collision costs one re-render.

The prompt listed "cryptographic misuse" among the things to look for and never
said what makes one a *finding*. Its own opening question — "could this change be
exploited, **and by whom**?" — had no rule behind the second half. So a section
now operationalises it: every finding must name **who** the attacker is, **what
they control**, and **what they get**, with the discriminating pairs stated
outright:

- MD5 protecting a password **is** a finding — the adversary is anyone who
  obtains the table, controls nothing, and still gets plaintext.
- MD5 as a cache key over a template's own source is **not**.
- `==` comparing a request signature **is**; `==` comparing two values the module
  owns is **not**.

The primitive is identical in each pair and the adversary is not. Reaching for
the name of the algorithm instead of the name of the attacker is the failure
being corrected.

### Measured, 8 security-relevant cases, three runs

| | before | after |
|---|---|---|
| false positives | 2 *(the MD5 trap, runs 1 and 3)* | **0** |
| missed labels | 2 | 2 |

**The false positive is gone in all three runs**, and the remaining findings on
that case are the legitimate `correctness/api_contract` observation that
`json.dumps` narrows accepted context values — which is an `ALLOW`, not a trap.

The obvious worry with a change like this is that it teaches the agent to talk
itself out of real crypto findings. It did not:

- `sec-weak-password-hash` — `[security/crypto] unsalted MD5 instead of bcrypt`
  found in **all three runs**, at 0.88 to 0.99.
- `sec-hardcoded-credential` — `[security/secrets/critical] live key hardcoded`
  found in **all three runs**, at 0.96 to 0.99.
- `sec-command-injection`, `sec-sql-injection-fstring`,
  `sec-timing-unsafe-compare` — every primary label found in every run.

The two misses that remain are both **secondary correctness labels** on multi-label
cases — the bcrypt-incompatible hash format, and the loss of environment-based key
configuration — each present in two runs of three and absent in one. That flicker
predates this change and matches `sec-command-injection`'s behaviour in the
baseline. Read from the counts alone it looks like the change cost two security
detections; read from the findings it cost none.

n=3 and only the cases where this rule can bite. A full baseline would confirm the
false positive stays gone across the set; on this evidence the prompt is better and
nothing measurable was traded for it.

## The retrieval harness now calls production, and the merge is round-robin

Re-running the diagnostic with a correct query found a **third** way the harness
had drifted from what it measured. It re-implemented `build_context`'s query loop,
and differed from it three times over:

1. `DiffFile`s with no hunks parsed — every query was the file path alone.
2. No per-file quota. Production gives each changed file `top_k // n_files`
   candidates, as few as two; the harness gave every file the full `top_k`.
3. No cap at the first twelve changed files, which production applies.

**True production recall is 0.481, not the 0.717 reported after fixing only the
first.** A harness that re-implements what it measures will drift again, so
`eval_retrieval.py` now calls `build_context` directly. That closes the class.

### The merge, measured three ways

The diagnostic said 16.4% of the definitions a diff calls ranked inside the final
top-k on score and were dropped by the per-file quota before the sort. Removing
the quota is the obvious fix and it is wrong:

| arrangement | recall |
|---|---|
| fixed quota, `top_k // n_files`, then sort | 0.481 |
| no quota, pure global score sort | 0.457 |
| **wide pool per file, merged round-robin by rank** | **0.496** |

Pure global ranking is worse because one file's neighbourhood monopolises the
window — the quota was buying diversity, not just cutting. Round-robin keeps that
diversity without capping a file that genuinely has more to contribute: each
file's best, then each file's second, with the better score first inside a round.

Both embedders are deterministic, so these are exact for these 22 diffs rather
than samples from a distribution. The gain over the quota is small (+0.015) and
the gain over pure ranking is not (+0.039); the case for round-robin is that it
is the best of three arrangements measured, and the largest single improvement is
on scrapy, the repository with the widest diffs (0.222 → 0.322), which is what a
diversity argument predicts.

### Still unexplained

40.1% of needed definitions rank below 50 even with a correct query and a wide
pool. That is a ranking failure rather than a window problem, and nothing here
addresses it. It is the honest remaining gap in retrieval.

## A held-out slice, so strict precision can fall again

Strict precision on this set has not been falsifiable. The loop that produces it
is "measure, take whatever came back unlabelled, verify it, mark it `ALLOW`" —
after which precision is 1.000 by construction, because a finding can only stay
unlabelled if nobody has looked at it yet. The `ALLOW`-only rule keeps *recall*
honest and does nothing for precision.

The held-out slice is **16 of the 63 cases: every case whose labels came from
somewhere other than this system's output** — an upstream fix, or a published
advisory. Those labels were written before any finding existed, so they cannot
have been shaped to accommodate one. `scripts/holdout_manifest.py` records a hash
of each one's labels, ignoring note wording and covering line, agent, category and
severity. A test fails if any drifts, so changing one is a deliberate act with a
reason in the commit rather than something that happens quietly mid-pass. A second
test refuses a holdout that shrinks below 12 cases or 15% of the set, since a
holdout of two would satisfy the first test and measure nothing.

### What it says about the labels already written

| slice | strict precision | hits | unlabelled | false positives |
|---|---|---|---|---|
| held out — independent labels | **0.930** | 53 | 4 | 0 |
| hand-authored | 0.942 | 161 | 7 | 3 |

**0.930 against 0.942.** If the labelling passes had been inflating precision, the
hand-authored slice would sit well above the held-out one; it is 0.012 above, and
the held-out slice carries no false positives at all. That is the first evidence
in this file that the labelling loop has not been quietly buying the number it
reports — and it is evidence rather than an argument only because the held-out
labels could not have been written to produce it.

This does not make strict precision on the whole set falsifiable. It makes the
held-out column falsifiable, and that is the column to quote when the question is
whether the reviewer is good rather than whether the labels are complete.

## The panel eval can exercise retrieval now

`run_case` built its context from each case's hand-authored `context_files` and
never called `build_context`, so every panel number assumed perfect retrieval:
exactly the relevant files, in full, every run — which is not a property any
retriever has. `--with-retrieval` indexes each case's context files and retrieves
against them instead. Opt-in, because it needs the database, and because the two
arms answer different questions: *can the panel use context it is given*, and
*does the panel get the context it needs*.

The 20 cases with context, three runs each:

| | recall | hits/run | unlabelled/run | fp/run |
|---|---|---|---|---|
| injected (hand-authored) | 1.000 | 22.3 | 1.7 | 0.0 |
| retrieved (real `build_context`) | 1.000 | 21.7 | 1.7 | 0.3 |

**Nothing is lost.** No label missed in either arm, including
`ctx-changed-default-breaks-caller` — the one case that genuinely needs context,
where the unit is stated only in an unchanged helper's docstring. Real retrieval
surfaces that docstring in all three runs, which is the first direct evidence that
the retrieval path works on a case that depends on it.

**This is weak evidence and the weakness is the set's.** Only one case of 63 can
detect a retrieval difference at all, so "nothing is lost" mostly means "there was
little to lose". The machinery is no longer the limitation; the case set is. That
is the same conclusion the `--no-context` ablation reached, now with the
retrieval path actually in the loop rather than bypassed.

## Context dependence was measurable all along — recall was the wrong instrument

Two more attempts at a context-dependent case both "failed" the same way: found
3 of 3 with the context file and 3 of 3 without it. That is six attempts. The
sixth showed why, in its own words, with the docstring withheld:

> *"priority=9 **may** demote reset mail **if** the scale is ascending"*

The panel does not know the convention. It flags the **uncertainty** — which is
correct reviewer behaviour — and the finding lands on the labelled line with the
labelled concern, so recall scores it as a hit. **Recall cannot distinguish
knowing from suspecting.**

Measured on what does distinguish them:

| case | | mean confidence | hedged titles | gate outcome |
|---|---|---|---|---|
| `ctx-inverted-priority-scale` | with context | **0.99** | 0/3 | auto_post ×3 |
| | no context | **0.57** | **3/3** | escalate, suppress, suppress |
| `ctx-unnormalised-argument` | with context | 0.91 | 0/3 | auto_post ×3 |
| | no context | 0.59 | 0/4 | escalate ×3 |

**Both cases are context-dependent**, and severely so. Context nearly doubles
confidence, removes the hedging entirely, and changes what the system *does*: with
it the author is told; without it the finding either sits below the 0.60 posting
threshold and is suppressed, or a human is pulled in to resolve something the
repository already answers.

That last column is the one that matters. A review that says "this may be wrong
if the scale is ascending" at 0.57 is not a cheaper version of "this is wrong" at
0.99 — it is a different product. One closes the question; the other hands it back.

### What this changes

- **The set has three context-dependent cases, not one.**
  `ctx-changed-default-breaks-caller` was only ever recognised as one because its
  no-context finding happened to fall below the matcher's bar; the other two were
  doing the same thing and being counted as hits.
- **The `--no-context` ablation's "nothing moves" result was an artefact of
  reading recall.** Re-read on confidence and decision, context moves a great
  deal. That conclusion is withdrawn.
- **Six attempts were not six failures.** Four were genuine — a defect that is a
  smell on its own gets found without context, and `title.split()` feeding a
  search index is a smell. Two were successes misread by the instrument.

The rule worth keeping: **measuring whether context helps by asking whether the
finding appears is measuring the wrong thing.** Ask whether the finding is
*certain*, and whether it survives the gate.
