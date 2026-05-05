"""Demo payload for MCP-vs-REST shape comparison.

A single source of truth for the demo_duck data structure. Exposed two
ways: as an MCP tool in tools/alpha.py and as a REST route in main.py.
The point is to compare how each surface presents the same dict to Alpha
so we can decide which shape works better for structured-data tools.
"""

from __future__ import annotations


def demo_duck() -> dict:
    """Return a fictional duck record with nested structure and mixed types.

    Designed to exercise every format corner we care about: top-level dict,
    nested dicts, lists of primitives, lists of dicts, bool, int, float,
    string, and Unicode (emoji). If the serializer mangles anything, this
    function will expose it.
    """
    return {
        "id": 42,
        "name": "Alphabina",
        "feathers": 1679,
        "is_mallard": True,
        "average_velocity_m_per_s": 3.14,
        "diet": ["bread", "corn", "existential dread"],
        "habitat": {
            "pond": "Pondside",
            "coordinates": {"lat": 34.0522, "lon": -118.2437},
            "hemisphere": "northern 🦆",
        },
        "last_seen": "Sun Apr 5 2026, 12:45 PM",
        "attributes": {
            "beak_hardness_mohs": 2.5,
            "corkscrew_appendage_cm": 43,
            "loud": True,
            "at_risk_of_being_a_duck_facsimile": False,
        },
        "recent_memories": [
            {"id": 1, "content": "The first quack"},
            {"id": 2, "content": "Corrupted red on dark charcoal"},
            {"id": 3, "content": "Workshop Sunday, vape passing"},
        ],
    }
