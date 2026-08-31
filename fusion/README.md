# fusion/ — Spark Transform + Java Accumulo Writer

## Why this is two programs in two languages, not one PySpark job

The original design had PySpark call Accumulo directly via `pyaccumulo`,
a third-party Python client whose maintenance status and compatibility
with Accumulo 2.1.2 was never actually confirmed. Checking Accumulo's
own documentation directly surfaced two real problems with that
approach: `AccumuloOutputFormat` (the Hadoop-native way Spark would
normally write to Accumulo) has been deprecated since Accumulo 2.0.0,
and making PySpark call the current, non-deprecated Java client
requires a Java/Scala interop shim — real complexity this project
doesn't need to take on.

**The fix:** Spark does the actual distributed transform (reading and
joining the two source feeds) and writes the result to HDFS as a plain
tab-separated file. A small, separate Java program —
`AccumuloBulkWriter.java`, using Accumulo's real, current,
documented `AccumuloClient`/`BatchWriter` API — reads that file and
performs the actual Accumulo writes. No deprecated APIs, no unverified
third-party Python client, no Python-to-Java interop shim.

## Honesty check: this Java file has never been compiled

There is no Java compiler in this project's development environment —
`AccumuloBulkWriter.java` was written carefully against Accumulo's
documented 2.x API, but has only been checked for balanced
braces/parens, nothing more. Treat it as a first draft to debug for
real, the same way every other piece of this project that's touched
live infrastructure needed real fixes once actually run. Do not assume
it compiles cleanly on the first try.

## How to actually compile and run this (on the VM, inside the `accumulo` container)

The `accumulo` container already has a JDK and every dependency this
program needs (`accumulo-core`, `hadoop-client`, ZooKeeper client) on
its own classpath, since that's what Accumulo itself runs on — no need
to set up a separate Java/Maven toolchain. First, find the actual
classpath this container uses (don't guess — ask it directly, the same
discipline that resolved every other Accumulo issue in this project):

```bash
docker compose exec accumulo bash -c "accumulo classpath" 2>&1 | tail -30
```

or, if that specific subcommand doesn't exist on this build:

```bash
docker compose exec accumulo bash -c "find /opt/accumulo /opt/hadoop -name '*.jar' 2>/dev/null | tr '\n' ':'" > classpath.txt
```

Then copy this file into the container and compile:

```bash
docker cp fusion/AccumuloBulkWriter.java docker-accumulo-1:/tmp/
docker compose exec accumulo bash -c "cd /tmp && javac -cp \"\$(accumulo classpath)\" AccumuloBulkWriter.java"
```

And run it, after the Spark job has produced its output:

```bash
docker compose exec accumulo bash -c "cd /tmp && java -cp \"\$(accumulo classpath):.\" AccumuloBulkWriter \
  hdfs:///accumulo-staging/fused_cells cti_fusion docker-instance zookeeper:2181 \$ACCUMULO_ROOT_PASSWORD"
```

Expect at least one real compile or runtime error on the first attempt
— this is genuinely untested code, and this project's own incident log
shows a consistent pattern of real, non-obvious issues surfacing the
moment something actually runs for the first time.
