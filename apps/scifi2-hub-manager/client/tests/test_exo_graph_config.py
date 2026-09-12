"""Guard the deployed server's single-input graph constraint."""
import json
from pathlib import Path


def test_exo_uses_auxiliary_subscription_not_second_graph_input():
    path = Path(__file__).resolve().parents[2] / "config/rhd2132_with_exo.json"
    config = json.loads(path.read_text())
    nodes = {node["id"]: node for node in config["nodes"]}
    app = next(n for n in nodes.values() if n.get("application", {}).get("name") == "scifi2-hub-manager")
    source_id = app["application"]["parameters"]["exo_source_node_id"]
    assert nodes[source_id]["type"] == "kBroadbandSource"
    destinations = [edge["dst_node_id"] for edge in config["connections"]]
    assert len(destinations) == len(set(destinations))
    inputs = [e["src_node_id"] for e in config["connections"] if e["dst_node_id"] == app["id"]]
    assert inputs == [1]
    assert source_id not in inputs
    for edge in config["connections"]:
        assert edge["src_node_id"] in nodes and edge["dst_node_id"] in nodes
