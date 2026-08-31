# Real Findings From Building This Project

Same rationale as every other project in this portfolio: an honest
account of what was actually discovered while writing and testing this
code, not a cleaned-up version of events.

## 1. The exact-match-vs-authorization-satisfaction distinction — a real, constructed security proof, not a hypothetical

While designing `project_to_elasticsearch()`, the obvious first
approach was: `table.scan(authorizations={"U"})` — reuse the same
authorization-checking scan already built and verified for Accumulo
access, just called with a `U`-only authorization set. This turns out
to be the WRONG design, and it's provable, not just theoretically
risky.

Constructed a deliberately adversarial cell labeled `"U|TS"` (an OR
expression mixing the unclassified label with Top Secret) and ran it
through both approaches:
- `scan(authorizations={"U"})` **admitted the cell** — correctly, per
  Accumulo's own OR semantics: holding `U` alone satisfies `U|TS`.
- The actual `project_to_elasticsearch()` implementation, which does
  **exact string match** on the literal label `"U"` rather than
  authorization-satisfaction, **correctly excluded it**.

This project's own data generator never actually produces a mixed
label like `"U|TS"` — every cell it creates is either purely `U` or
purely classified. So this specific bug could not have been triggered
by this project's own synthetic data. It's included anyway, as a
genuine, provable defense against a plausible future/real-world
scenario: a legacy or mislabeled source system producing a cell with a
mixed classification expression. The distinction matters specifically
*because* Elasticsearch has no per-query authorization check at all
once data lands there — unlike Accumulo, which re-evaluates
authorizations on every single scan. A projection step feeding an
unguarded system needs to be more conservative than "would a U-holder
be allowed to see this," and exact-match is the way to guarantee that.

`tests/test_security.py::test_projection_excludes_mixed_classification_cell`
encodes this exact constructed scenario as a permanent regression test.

## 2. Accumulo's real ambiguity rule (mixed & and | without parentheses) had to be implemented deliberately, not just parsed permissively

Early versions of the parser considered simply giving `&` higher
precedence than `|` (standard operator-precedence parsing, the way
most expression languages handle mixed operators) rather than
rejecting the mixed case outright. Checked this against Accumulo's own
documented behavior: real Accumulo actually **rejects** an expression
like `A&B|C` as ambiguous, requiring explicit parentheses
(`(A&B)|C` or `A&(B|C)`) instead of silently picking a precedence
convention. Implemented `parse_visibility()` to match this — raising
`VisibilityParseError` on any mixed-operator expression without
parentheses — rather than the more permissive (but non-matching)
precedence-based approach, since silently picking a different
precedence convention than the real system this project mirrors would
be a subtle, dangerous correctness gap: a security label that parses
differently in this project's simulator than it would in real
Accumulo defeats the entire point of building the simulator to
validate this logic in the first place.

## 3. Running this for real: eleven distinct root causes, found on a real Azure VM

Everything below happened running the actual `docker-compose.yml`
stack (trimmed to ZooKeeper, HDFS, Accumulo, Elasticsearch, Kibana) on
a real Azure `Standard_D4s_v4` VM (4 vCPU, 16GB RAM) — genuinely
different territory from the pure-Python logic above, and, as
predicted, it did not work on the first attempt. Every fix below was
driven by an actual error message or a direct inspection of the
running system, not guessed.

**3a. `apache/accumulo:2.1.2` is not a real Docker Hub image.**
First failure: `pull access denied for apache/accumulo, repository
does not exist`. Confirmed by checking Apache's own `accumulo-docker`
GitHub repo directly — its README states plainly there is no official
prebuilt image yet, and you must build it yourself. Fixed by cloning
that repo and running its own `docker build`, which — coincidentally —
defaults to exactly Accumulo 2.1.2 + Hadoop 3.3.6 + ZooKeeper 3.8.2,
matching the versions already chosen elsewhere in this compose file.

**3b. `fs.defaultFS` was never actually configured — Hadoop needs XML
config files, not environment variables.** The `namenode` container
crashed with `Invalid URI for NameNode address (check fs.defaultFS):
file:/// has no authority`. The original compose file used a made-up
environment variable (`ENSURE_NAMENODE_DIR`) that this image never
recognized — Hadoop's actual configuration model is XML files
(`core-site.xml`, `hdfs-site.xml`), not arbitrary env vars the way
Kafka or Elasticsearch support. Fixed by writing real config files and
mounting them into the container.

**3c. HDFS needs a one-time format, and a conditional "format only if
needed" check failed in an unclear way.** A first attempt at an
idempotent startup command (`if [ ! -d .../current ]; then format; fi`)
failed with `storage directory does not exist or is not accessible`
even on a freshly-wiped volume — the exact cause wasn't conclusively
identified. Simplified to always format on every startup instead
(accepting that data doesn't survive a restart, a deliberate,
documented trade-off favoring reliability over idempotency for this
demo).

**3d. A Docker named volume is root-owned by default; this image's
process is not.** Even with formatting always attempted, it failed with
`Cannot create directory /tmp/hadoop/dfs/name/current` — a classic
Docker volume-ownership mismatch. Fixed by explicitly running the
`namenode`/`datanode` containers as `user: "root"`.

**With 3a–3d fixed, HDFS came up completely and was verified two
independent ways**: its own logs showing `Leaving safe mode`, and
directly querying its JMX endpoint from outside the container
(`curl http://localhost:9870/jmx?...`), which returned `"State":
"active"`.

**3e. The Accumulo image's default behavior is to print help text and
exit cleanly (code 0) — it needs an explicit server command.** The
first `accumulo` container attempt showed `Exited (0)` with no error at
all, because with no command override it just ran `accumulo help`.

**3f. A `command:` override does not replace an image's `ENTRYPOINT` —
it gets appended to it.** The first fix attempt (`command: bash -c
"..."`, with no entrypoint change) resulted in an effective command of
`accumulo bash -c "..."` — the `accumulo` script tried to interpret
`bash` as a Java class name to run, and failed. Confirmed directly from
the `COMMAND` column in `docker compose ps`. Fixed by explicitly
clearing the entrypoint (`entrypoint: []`).

**3g. Clearing the entrypoint alone then failed with `JAVA_HOME is not
set`, even though it was confirmed present via a manual diagnostic.**
The manual test (`docker compose run --entrypoint bash accumulo -c
"..."`) used clean exec-form invocation and worked; the compose file's
`command:` was a plain YAML string, which Compose treats as
**shell-form**, implicitly wrapping it in an extra `/bin/sh -c "..."`
layer that lost `JAVA_HOME` along the way. Fixed by using exec-form
(YAML lists) for both `entrypoint` and `command`, matching the shape of
the manual test that worked.

**3h. Accumulo has a THIRD, separate configuration layer
(`accumulo.properties`) that was never touched.** Even with Hadoop's
own config correctly mounted, Accumulo's logs showed `Accumulo data
dirs are [[hdfs://localhost:8020/accumulo]]` and `Zookeeper server is
localhost:2181` — still hardcoded to `localhost`. Confirmed by directly
inspecting the image's default `/opt/accumulo/conf/accumulo.properties`
file, which hardcodes both `instance.volumes` and
`instance.zookeeper.host`. Fixed by mounting a corrected version of
this file, same pattern as the Hadoop config fix.

**3i. A bash operator-precedence bug caused a genuine race
condition.** `init && manager & tserver & wait` does not mean "run
init, then start manager and tserver in parallel" — `&` has lower
precedence than `&&`, so this actually parsed as `(init && manager) &`
running as one background job, with `tserver &` starting as a
completely separate, parallel job with no dependency on `init`
finishing. Confirmed by the resulting crash: `Thread 'tserver' died ...
Accumulo not initialized` — tserver genuinely raced ahead of init
completing. Fixed by making `init` a real sequential statement with an
explicit exit-code check, only starting `manager`/`tserver` afterward.
Also fixed a second, related bug found in the same command: a bare
`wait` always returns exit code 0 regardless of whether the background
jobs actually succeeded, silently masking exactly this kind of failure
— changed to `wait -n` to surface the real exit code.

**3j. HDFS's own internal permission model blocked Accumulo from
creating its own directory.** Even with the config and sequencing
fixed, `init` failed with `Permission denied: user=accumulo,
access=WRITE, inode="/":root:supergroup`. HDFS's root directory is
owned by `root`, and the Accumulo container runs as a non-root
`accumulo` user — a real permission layer independent of the Docker
volume-ownership issue in 3d. Fixed by manually creating `/accumulo` in
HDFS and chowning it to `accumulo:supergroup` from the `namenode`
container (which does run as root).

**3k. Two distinct confirmation prompts exist in Accumulo's own init
code, and only one was suppressed.** With every prior issue fixed, init
still failed with a bare `NullPointerException` at
`Initialize.getInstanceNamePath`. Checked Accumulo's actual source code
directly: `-f`/`--force` and `--clear-instance-name` suppress two
**separate** prompts — the former for security-info reset, the latter
for "this instance name already exists." Repeated retries against the
same ZooKeeper state (without a full volume wipe each time) had left a
stale `docker-instance` registration behind, triggering the second
prompt, which calls `System.console().readLine()` — returning `null`
with no tty attached to a background daemon container, causing the
NPE. Fixed by adding `--clear-instance-name` alongside `-f`.

**With all of 3a–3k fixed, Accumulo genuinely started and was verified
independently, two ways**: its own logs showing the Root and Metadata
system tables being assigned, hosted, and written to, and — the
stronger proof — directly listing HDFS's filesystem from the outside
(`hdfs dfs -ls -R /accumulo`), showing real `.rf` data files and
write-ahead logs, owned by `accumulo:supergroup`, independent of
anything Accumulo itself self-reported.

**One remaining loose end, not chased further**: the Accumulo Monitor
web UI (port 9995) did not come up, since only `manager` and `tserver`
were started in the background command — `monitor` was never included.
Not required to prove the core thesis (cell-level security), so left
as a known, minor gap rather than a blocking issue.

## 4. Testing real persistence across an actual VM restart: six more distinct findings

Everything in incident #3 happened within one continuous VM session —
the stack was never actually stopped and restarted. Deallocating the
VM overnight (to stop billing) and restarting it the next day was the
first genuine test of whether this stack survives a real restart, and
it surfaced a fresh set of real problems, none of which were visible
during yesterday's continuous session.

**4a. The Docker Compose plugin was gone after the VM restart.**
`docker compose` failed with `'compose' is not a docker command`, and
`docker --version` showed `25.0.7` — a different version than what
compose had been running against. The most likely explanation:
Ubuntu's default unattended-upgrades silently altered the Docker
installation overnight. Fixed by reinstalling the plugin explicitly
(`apt-get install --reinstall docker-compose-plugin`).

**4b. The original SSH private key genuinely wasn't recoverable — a
fresh Cloud Shell session is a different filesystem, not the same
machine.** Attempting to SSH back into the VM using the same commands
as before failed with `Permission denied (publickey)`, despite the
host key fingerprint matching (confirming it was genuinely the right
VM). The private key generated by the original `az vm create
--generate-ssh-keys` simply wasn't present in this new Cloud Shell
session's `~/.ssh/`. Fixed by generating a fresh keypair and adding its
public half to the VM directly via `az vm user update
--ssh-key-value`, which doesn't require already being logged in.

**4c. A related mix-up: several commands were accidentally run in
Cloud Shell instead of the actual VM.** The `sudo`/`apt-get` commands
from finding 4a initially failed with `sudo: the "no new privileges"
flag is set` and `apt-get: command not found` — not because those
tools were broken, but because the session was actually still in Azure
Cloud Shell (a managed, deliberately locked-down container never meant
to run the real Docker stack), identifiable by its distinct prompt
style. Once genuinely SSH'd into the VM (confirmed via `whoami` /
`hostname`), the same commands worked normally.

**4d. `namenode`'s "always reformat on startup" design (from incident
#3c) was fundamentally incompatible with the very thing being tested
today.** After the VM restart, `accumulo` failed with the exact same
"Permission denied creating /accumulo" error from yesterday — because
`namenode` reformats HDFS from scratch on every single start,
wiping the manually-chowned `/accumulo` directory each time. This also
explained `datanode` exiting with `Incompatible clusterIDs`: a freshly
reformatted `namenode` gets a new cluster ID that no longer matches
`datanode`'s own persisted storage, and HDFS correctly refuses to let
a mismatched datanode join. Fixed properly this time by checking for
the actual `VERSION` file (the file Hadoop writes as the final step of
a successful format) before reformatting, rather than yesterday's
directory-existence check that had behaved oddly. Confirmed working:
after this fix, a full stack restart showed `namenode: Healthy` and
`datanode: Up` immediately, with matching cluster IDs.

**4e. A real `depends_on` race condition: container "started" is not
the same as service "ready."** Bringing up all six services in one
single command sometimes had `accumulo` fail with `Connection refused`
to `namenode:9000`, even though `namenode`'s own web UI (port 9870)
was already reporting healthy — the web UI and the actual RPC port
don't necessarily become ready at the same moment, and `depends_on`
alone only waits for a container to start, not for the service inside
it to be genuinely ready. Fixed with a real Docker Compose
healthcheck on `namenode` (testing actual RPC connectivity via `hdfs
dfsadmin -report`) and `condition: service_healthy` on the
dependents' `depends_on` entries.

**4f. `--clear-instance-name`'s real function is destructive, not just
prompt-suppressing — confirmed by reasoning, though not the ultimate
root cause of the final symptom.** After fixing 4d, `accumulo` no
longer failed on the HDFS-level "already initialized" check, but
`manager` and `tserver` then died with a `NullPointerException` in
`lookupInstanceName`. Since the startup script unconditionally passed
`--clear-instance-name` on every run — including the "already
initialized, skip real init" case — and that flag's actual documented
function is to *delete* an existing instance name registration (not
merely suppress a confirmation prompt), it was suspected of silently
wiping the very name mapping `manager`/`tserver` needed. Fixed by only
passing `--clear-instance-name` in the narrow case of a genuine name
collision, detected from `init`'s own output text, rather than
routinely on every restart.

**4g. The actual root cause of 4f's symptom: ZooKeeper had no
persistent volume at all.** Even with 4f's fix applied, the identical
`NullPointerException` recurred on the very next restart — immediately
after a freshly successful init, with no repeated `init` calls
involved at all, disproving 4f as the root cause of *this specific*
recurrence (4f was still a real, worthwhile fix in its own right).
Checking `zookeeper`'s service definition directly revealed it had
never had a `volumes:` entry — meaning every container restart wiped
ZooKeeper's entire state from scratch, including the instance-name-to-
ID mapping Accumulo depends on. Accumulo's actual table *data* lives
safely in HDFS (which does persist correctly); the *metadata* linking
the human-readable name to that data lives in ZooKeeper, which was
silently reset to empty on every restart, unrelated to anything
Accumulo's own init logic does. Fixed by adding persistent volumes
(`/data`, `/datalog`) to `zookeeper`, matching every other stateful
service in this stack.

**Confirmed working end to end**: after all of 4a–4g, a full
`docker compose down` followed by `docker compose up -d` for all six
services came back correctly with **zero manual intervention** —
`namenode` and `zookeeper` both reporting healthy/running immediately,
`accumulo` correctly detecting its already-initialized state and
skipping straight to a genuinely healthy `manager`/`tserver`, including
real crash-recovery log messages (`recovered 0 mutations ... from 0
walogs`) confirming Accumulo's own internal consistency checks passed
cleanly against the persisted data.

## What's verified, and what genuinely isn't yet

Everything in `security/`, `ingestion/`, and `tests/` runs with zero
external dependencies beyond the Python standard library — no network,
no JVM, no real cluster. All 19 tests pass, including the full
end-to-end demonstration (100 entities, 574 total cells, three
different clearance levels tested, zero classified values ever
appearing in the Elasticsearch projection).

As of incident #3, HDFS and Accumulo were first run for real on a live
Azure VM. As of incident #4, this stack has now been confirmed to
**survive a genuine stop/restart cycle end to end with zero manual
steps** — a meaningfully stronger and more production-relevant claim
than "ran once in a continuous session," since real deployments get
restarted, redeployed, and rebooted routinely.
`fusion/spark_fusion_job.py` and `fusion/es_projection_writer.py` (the
actual Spark job and ES indexer, as opposed to the infrastructure
they'd run against) remain untested against this now-working,
now-persistent cluster — the immediate next step. See
`docs/architecture.md` for the current, updated status.
