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
