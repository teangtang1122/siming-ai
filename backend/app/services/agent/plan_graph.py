"""Pure data structures for agent plan execution graphs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class StepDef:
    """Definition of a single step in a plan graph."""
    tool: str
    args: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    retry_policy: str = "none"  # "none", "auto", "always"
    idempotency_key: str | None = None
    label: str = ""


@dataclass
class PlanGraph:
    """Directed acyclic graph of tool execution steps."""
    name: str
    steps: dict[str, StepDef] = field(default_factory=dict)

    def topological_order(self) -> list[str]:
        """Return step keys in dependency-respecting execution order."""
        in_degree: dict[str, int] = {k: 0 for k in self.steps}
        adjacency: dict[str, list[str]] = {k: [] for k in self.steps}
        for key, step in self.steps.items():
            for dep in step.depends_on:
                if dep in self.steps:
                    adjacency[dep].append(key)
                    in_degree[key] += 1

        queue = deque(k for k, d in in_degree.items() if d == 0)
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self.steps):
            raise ValueError("Cycle detected in plan graph")
        return order

    def ready_steps(self, completed: set[str]) -> list[str]:
        """Return step keys whose dependencies are all in `completed`."""
        return [
            key for key in self.topological_order()
            if key not in completed and all(dep in completed for dep in self.steps[key].depends_on)
        ]

    def downstream_keys(self, key: str) -> list[str]:
        """Return step keys that transitively depend on `key`, in topo order."""
        order = self.topological_order()
        try:
            start_idx = order.index(key)
        except ValueError:
            return []

        downstream_set: set[str] = set()
        queue = deque([key])
        while queue:
            node = queue.popleft()
            for other_key, step in self.steps.items():
                if node in step.depends_on and other_key not in downstream_set:
                    downstream_set.add(other_key)
                    queue.append(other_key)

        return [k for k in order[start_idx + 1:] if k in downstream_set]
