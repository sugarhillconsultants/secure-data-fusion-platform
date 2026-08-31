/*
 * fusion/AccumuloBulkWriter.java
 *
 * Reads the TSV file spark_fusion_job.py writes to HDFS and loads it
 * into Accumulo via BatchWriter — using the CURRENT, documented
 * AccumuloClient API (Accumulo.newClient()...build()), NOT the
 * deprecated 1.x ZooKeeperInstance/Connector pattern, and NOT the
 * deprecated AccumuloOutputFormat MapReduce integration. See
 * fusion/spark_fusion_job.py's module docstring and
 * docs/architecture.md for the full reasoning behind this split
 * design (Spark for transform, this program for the actual write).
 *
 * DELIBERATELY A PLAIN JAVA PROGRAM, NOT A SPARK JOB ITSELF: this
 * avoids needing a Java/Scala interop shim to bridge PySpark's RDD API
 * into Accumulo's Java client — a real, added complexity this
 * project's scope doesn't need to take on when a small, focused
 * program does the same job more simply and reliably.
 *
 * Intended to be compiled and run INSIDE the already-running
 * `accumulo` container, using its own bundled classpath (accumulo-core,
 * hadoop-client, zookeeper client are all already present there, since
 * that's what Accumulo itself runs on) — avoiding the need to set up a
 * separate Java/Maven toolchain from scratch. See docs/ for the exact
 * compile/run commands, confirmed against the real container rather
 * than assumed.
 *
 * NOT YET COMPILED OR RUN — written against Accumulo 2.1.2's
 * documented current API, but this is genuinely the first time this
 * exact code has been checked against a real compiler. Expect this to
 * need at least one real fix once actually attempted, consistent with
 * every other piece of this project that's touched live infrastructure.
 */

import org.apache.accumulo.core.client.Accumulo;
import org.apache.accumulo.core.client.AccumuloClient;
import org.apache.accumulo.core.client.BatchWriter;
import org.apache.accumulo.core.client.BatchWriterConfig;
import org.apache.accumulo.core.data.Mutation;
import org.apache.accumulo.core.security.ColumnVisibility;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.fs.FileSystem;
import org.apache.hadoop.fs.FileStatus;
import org.apache.hadoop.fs.Path;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

public class AccumuloBulkWriter {

    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            System.err.println("Usage: AccumuloBulkWriter <hdfsInputDir> <accumuloTable> "
                + "<instanceName> <zookeepers> <password>");
            System.exit(1);
        }

        String hdfsInputDir = args[0];
        String tableName = args[1];
        String instanceName = args[2];
        String zookeepers = args[3];
        String password = args[4];
        String username = "root"; // matches this project's docker-compose ACCUMULO_ROOT_PASSWORD setup

        AccumuloClient client = Accumulo.newClient()
                .to(instanceName, zookeepers)
                .as(username, password)
                .build();

        if (!client.tableOperations().exists(tableName)) {
            client.tableOperations().create(tableName);
            System.out.println("Created table: " + tableName);
        }

        Configuration hadoopConf = new Configuration();
        FileSystem hdfs = FileSystem.get(hadoopConf);

        BatchWriterConfig bwConfig = new BatchWriterConfig();
        BatchWriter writer = client.createBatchWriter(tableName, bwConfig);

        long totalRows = 0;

        // Spark's .csv() writer produces multiple part-files in the
        // output directory (one per partition) — read all of them,
        // not just a single file.
        FileStatus[] partFiles = hdfs.listStatus(new Path(hdfsInputDir));
        for (FileStatus partFile : partFiles) {
            String fileName = partFile.getPath().getName();
            if (!fileName.startsWith("part-")) {
                continue; // skip Spark's _SUCCESS marker file and any others
            }

            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(hdfs.open(partFile.getPath()), StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    if (line.isEmpty()) continue;

                    // Matches spark_fusion_job.py's column order exactly:
                    // row, column_family, column_qualifier, column_visibility, timestamp, value
                    String[] fields = line.split("\t", 6);
                    if (fields.length != 6) {
                        System.err.println("WARNING: skipping malformed line (expected 6 tab-separated "
                            + "fields, got " + fields.length + "): " + line);
                        continue;
                    }

                    String row = fields[0];
                    String columnFamily = fields[1];
                    String columnQualifier = fields[2];
                    String columnVisibility = fields[3]; // passed through UNCHANGED - see spark job docstring
                    long timestamp = Long.parseLong(fields[4]);
                    String value = fields[5];

                    Mutation mutation = new Mutation(row);
                    mutation.put(columnFamily, columnQualifier,
                            new ColumnVisibility(columnVisibility), timestamp, value);
                    writer.addMutation(mutation);
                    totalRows++;
                }
            }
        }

        writer.close();
        client.close();
        hdfs.close();

        System.out.println("Loaded " + totalRows + " cells into Accumulo table '" + tableName + "'");
    }
}
