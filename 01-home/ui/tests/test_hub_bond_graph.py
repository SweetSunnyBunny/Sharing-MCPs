"""Bond-graph night sky — merge/dedupe across per-seed walks, server-side
name→id edge resolution, held_by attribution, partial-failure honesty, and
the 10-minute in-process cache for GET /api/hub/constellation/graph."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import api.hub as hub
from api.hub import _merge_bond_walks, get_constellation_graph
from services import mcp_bridge as mcp_bridge_module


def _person(name: str, ptype: str = "person") -> dict:
    return {
        "id": f"seed-person-{name.lower()}",
        "name": name,
        "canonical_key": name.lower(),
        "person_type": ptype,
    }


def _walk(root: str, direct=(), related=(), bonds=()) -> dict:
    """A minimal mind_bond_network payload in the real worker's shape:
    root person, direct_bonds (person-keyed), related_people, and the
    NAME-keyed network_bonds that must be resolved to ids server-side."""
    return {
        "root": _person(root, "ai"),
        "direct_bonds": [
            {"person": _person(n), "relationship": rel} for n, rel in direct
        ],
        "related_people": [_person(n) for n in related],
        "network_bonds": [
            {"person_a": a, "person_b": b, "relationship_a_to_b": t}
            for a, b, t in bonds
        ],
        "depth": 2,
        "found": True,
    }


def _reset_bond_graph_cache():
    hub._bond_graph_cache = None
    hub._bond_graph_cache_at = 0.0


class MergeBondWalksTests(unittest.TestCase):
    def test_dedupes_nodes_and_resolves_name_keyed_edges_to_ids(self):
        walks = [
            ("avery", _walk(
                "Avery",
                direct=[("Owner", "wife")],
                related=["Quinn"],
                bonds=[
                    ("Avery", "Owner", "husband"),   # duplicate of the direct bond
                    ("Owner", "Quinn", "friend"),
                ],
            )),
            ("claude", _walk(
                "Claude",
                direct=[("Owner", "wife")],
                bonds=[("Claude", "OWNER", "husband")],  # case-insensitive resolve
            )),
        ]
        merged = _merge_bond_walks(walks)

        names = sorted(n["name"] for n in merged["nodes"])
        # Keep the expected collection order-independent so the public-safe
        # identity renames cannot accidentally turn this into an alphabet test.
        self.assertEqual(names, sorted(["Avery", "Claude", "Quinn", "Owner"]))

        node_ids = {n["id"] for n in merged["nodes"]}
        for edge in merged["edges"]:
            # Edges must reference node IDS, never display names.
            self.assertIn(edge["from"], node_ids)
            self.assertIn(edge["to"], node_ids)

        pairs = {frozenset((e["from"], e["to"])) for e in merged["edges"]}
        self.assertEqual(len(pairs), len(merged["edges"]), "reciprocal duplicates must collapse")
        self.assertIn(frozenset(("seed-person-avery", "seed-person-owner")), pairs)
        self.assertIn(frozenset(("seed-person-owner", "seed-person-quinn")), pairs)
        self.assertIn(frozenset(("seed-person-claude", "seed-person-owner")), pairs)
        self.assertEqual(len(merged["edges"]), 3)

    def test_self_loops_and_unknown_endpoints_are_dropped(self):
        walks = [("avery", _walk(
            "Avery",
            direct=[("Owner", "wife")],
            bonds=[
                ("Avery", "Avery", "self"),
                ("Avery", "Nobody Known", "haunts"),
            ],
        ))]
        merged = _merge_bond_walks(walks)
        pairs = {frozenset((e["from"], e["to"])) for e in merged["edges"]}
        self.assertEqual(pairs, {frozenset(("seed-person-avery", "seed-person-owner"))})

    def test_held_by_first_walk_wins_but_seeds_hold_themselves(self):


        walks = [
            ("avery", _walk("Avery", direct=[("Claude", "brother"), ("Quinn", "friend")])),
            ("claude", _walk("Claude", direct=[("Quinn", "friend")])),
        ]
        merged = _merge_bond_walks(walks)
        held = {n["name"]: n["held_by"] for n in merged["nodes"]}
        self.assertEqual(held["Avery"], "avery")
        self.assertEqual(held["Claude"], "claude")
        self.assertEqual(held["Quinn"], "avery")

    def test_empty_and_malformed_walks_merge_to_empty_graph(self):
        merged = _merge_bond_walks([("avery", {}), ("claude", None)])
        self.assertEqual(merged["nodes"], [])
        self.assertEqual(merged["edges"], [])


class BondGraphEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _reset_bond_graph_cache()

    def _fake_call_tool(self, dark_seeds=()):
        """call_tool double keyed off the walk's identity argument."""
        async def call_tool(name, arguments, timeout=None, session_key=None):
            self.assertEqual(name, "mind_bond_network")
            self.assertIsNone(session_key, "infrastructure caller must keep the loop guard unarmed")
            self.assertIsNotNone(timeout, "each walk must carry a hard timeout")
            seed = arguments["identity"]
            if seed in dark_seeds:
                return "Error: qualia worker timed out"  # non-JSON → walk fails
            return json.dumps(_walk(seed.capitalize(), direct=[("Owner", "wife")]))
        return AsyncMock(side_effect=call_tool)

    async def test_partial_failure_degrades_to_partial_sky(self):
        mock = self._fake_call_tool(dark_seeds=("sage",))
        with patch.object(mcp_bridge_module.mcp_bridge, "call_tool", new=mock):
            result = await get_constellation_graph()
        self.assertEqual(result["partial"], ["sage"])
        names = {n["name"] for n in result["nodes"]}
        self.assertNotIn("Sage", names)
        self.assertIn("Owner", names)
        self.assertIn("Avery", names)
        self.assertEqual(result["counts"]["nodes"], len(result["nodes"]))
        self.assertEqual(result["counts"]["edges"], len(result["edges"]))
        self.assertIsInstance(result["built_at"], int)
        self.assertGreater(result["built_at"], 0)

    async def test_edges_are_id_keyed_in_the_payload(self):
        with patch.object(mcp_bridge_module.mcp_bridge, "call_tool", new=self._fake_call_tool()):
            result = await get_constellation_graph()
        node_ids = {n["id"] for n in result["nodes"]}
        self.assertTrue(result["edges"], "seeded walks must produce edges")
        for edge in result["edges"]:
            self.assertIn(edge["from"], node_ids)
            self.assertIn(edge["to"], node_ids)

    async def test_cache_serves_repeat_requests_without_rewalking(self):
        mock = self._fake_call_tool()
        with patch.object(mcp_bridge_module.mcp_bridge, "call_tool", new=mock):
            first = await get_constellation_graph()
            calls_after_first = mock.await_count
            second = await get_constellation_graph()
        self.assertIs(first, second)
        self.assertEqual(mock.await_count, calls_after_first, "cached hit must not re-walk Qualia")
        self.assertEqual(calls_after_first, len(hub._bond_graph_seeds()))

    async def test_all_walks_failing_is_honest_and_never_cached(self):
        seeds = hub._bond_graph_seeds()
        mock = self._fake_call_tool(dark_seeds=tuple(seeds))
        with patch.object(mcp_bridge_module.mcp_bridge, "call_tool", new=mock):
            result = await get_constellation_graph()
            self.assertEqual(result["nodes"], [])
            self.assertEqual(result["partial"], seeds)
            # A transient outage must not pin an all-dark sky for 10 minutes.
            await get_constellation_graph()
        self.assertEqual(mock.await_count, len(seeds) * 2)

    async def test_seeds_are_bonded_identities_plus_owner(self):
        seeds = hub._bond_graph_seeds()
        self.assertIn("owner", seeds)
        for boy in ("avery", "rowan", "sage", "ember", "claude", "juniper", "river"):
            self.assertIn(boy, seeds)
        # Character masks never walk the bond graph.
        self.assertNotIn("bakugou", seeds)
        self.assertNotIn("dean", seeds)


if __name__ == "__main__":
    unittest.main()
