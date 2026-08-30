"""
fusion/es_projection_writer.py

Takes the output of security.accumulo_sim.project_to_elasticsearch()
(or, in production, a real Accumulo scan filtered the same way) and
bulk-indexes it into Elasticsearch for the Kibana hunt layer.

Deliberately calls the SAME project_to_elasticsearch() function already
verified in tests/test_security.py, rather than reimplementing the
projection logic here against a live Accumulo connection — this way,
the exact-match security guarantee proven in tests applies to whatever
actually gets indexed, not a second, unverified copy of similar logic.

Not executed against a real Elasticsearch cluster in this project's
development environment — see docker/ to run this for real.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from elasticsearch import Elasticsearch, helpers
from security.accumulo_sim import AccumuloTableSim, project_to_elasticsearch

ES_HOST = os.environ.get("ES_HOST", "http://elasticsearch:9200")
ES_INDEX = "cti-hunt-unclassified"


def index_projection(table: AccumuloTableSim):
    docs = project_to_elasticsearch(table)

    es = Elasticsearch(ES_HOST)

    actions = [
        {"_index": ES_INDEX, "_id": doc["entity_id"], "_source": doc}
        for doc in docs
    ]

    success_count, errors = helpers.bulk(es, actions, raise_on_error=False)
    print(f"Indexed {success_count} documents into '{ES_INDEX}'")
    if errors:
        print(f"WARNING: {len(errors)} documents failed to index: {errors[:5]}")

    return success_count, errors


if __name__ == "__main__":
    # In a real run, `table` would be populated from an actual Accumulo
    # scan rather than constructed here — this entry point exists for
    # manual testing against the docker-compose stack.
    print("Run this via fusion/spark_fusion_job.py's pipeline, or manually "
          "against a populated AccumuloTableSim for local testing.")
