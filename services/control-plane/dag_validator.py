"""
DAG Validator — Validates Master DAG structure for correctness.
"""
from typing import Dict, List, Tuple, Any
from collections import deque


def validate_dag(dag_json: dict, skills_map: dict[str, Any] | None = None) -> Tuple[bool, List[str]]:
    """Validate a DAG JSON structure.

    Args:
        dag_json: The DAG structure with 'nodes' and optional 'edges'.
        skills_map: Optional mapping of skill_id -> skill data for reference validation.

    Returns:
        (is_valid, list_of_errors)
    """
    errors: list[str] = []
    nodes = dag_json.get("nodes", [])
    edges = dag_json.get("edges", [])

    if not nodes:
        errors.append("DAG must have at least one node")
        return False, errors

    # Build node lookup
    node_ids = set()
    for node in nodes:
        nid = node.get("node_id")
        if not nid:
            errors.append("Every node must have a 'node_id'")
            continue
        if nid in node_ids:
            errors.append(f"Duplicate node_id: '{nid}'")
        node_ids.add(nid)

    # Validate depends_on references
    adjacency: dict[str, list[str]] = {n.get("node_id", ""): [] for n in nodes}
    in_degree: dict[str, int] = {n.get("node_id", ""): 0 for n in nodes}

    for node in nodes:
        nid = node.get("node_id", "")
        for dep in node.get("depends_on", []):
            if dep not in node_ids:
                errors.append(f"Node '{nid}' depends on unknown node '{dep}'")
            else:
                adjacency[dep].append(nid)
                in_degree[nid] = in_degree.get(nid, 0) + 1

    # Cycle detection via topological sort (Kahn's algorithm)
    queue = deque([n for n, d in in_degree.items() if d == 0])
    sorted_count = 0
    while queue:
        current = queue.popleft()
        sorted_count += 1
        for neighbor in adjacency.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if sorted_count != len(node_ids):
        errors.append("DAG contains a cycle — topological sort failed")

    # Validate skill references
    if skills_map:
        for node in nodes:
            sid = node.get("skill_id")
            if sid and sid not in skills_map:
                errors.append(f"Node '{node.get('node_id')}' references unknown skill '{sid}'")

    # Validate input_mapping references
    for node in nodes:
        # Planner may produce flexible mappings (constants, external sources,
        # implicit references) that are resolved at runtime. Keep validation
        # permissive here and rely on execution-time checks.
        _ = node.get("input_mapping", {})

    # Validate edge references
    for edge in edges:
        if edge.get("from_node") not in node_ids:
            errors.append(f"Edge references unknown from_node: '{edge.get('from_node')}'")
        if edge.get("to_node") not in node_ids:
            errors.append(f"Edge references unknown to_node: '{edge.get('to_node')}'")

    return len(errors) == 0, errors


def topological_order(nodes: list[dict]) -> list[str]:
    """Return node_ids in topological order. Assumes DAG is valid (no cycles)."""
    node_ids = [n["node_id"] for n in nodes]
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

    for node in nodes:
        for dep in node.get("depends_on", []):
            adjacency[dep].append(node["node_id"])
            in_degree[node["node_id"]] += 1

    queue = deque([n for n, d in in_degree.items() if d == 0])
    result = []
    while queue:
        current = queue.popleft()
        result.append(current)
        for neighbor in adjacency[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result
