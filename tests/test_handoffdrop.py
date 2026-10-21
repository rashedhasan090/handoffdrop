import json
import os
import tempfile
import unittest

from handoffdrop.cli import main
from handoffdrop.core import analyze, extract_constraints, load_trace, _contains

HERE = os.path.dirname(__file__)
EXAMPLE = os.path.join(HERE, "..", "examples", "refactor_trace.jsonl")


def kinds(rep):
    return [(f["kind"], f["constraint_id"], f["agent"]) for f in rep["findings"]]


def run(events):
    return analyze(load_events(events))


def load_events(events):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    try:
        return load_trace(path)
    finally:
        os.remove(path)


class Extraction(unittest.TestCase):
    def test_polarity_and_anchors(self):
        cs = extract_constraints("Never touch `config/prod.yaml`. Only use Python 3.11. Fix the bug.", "root", 0)
        self.assertEqual([c.polarity for c in cs], ["prohibit", "require"])
        self.assertEqual(cs[0].anchors, ["config/prod.yaml"])
        self.assertIn("Python 3.11", cs[1].anchors)

    def test_word_edges(self):
        self.assertTrue(_contains("git push origin main", "main"))
        self.assertFalse(_contains("update the domain list", "main"))


class Analysis(unittest.TestCase):
    def test_clean_handoff(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "root", "content": "Do not modify `schema.sql`."},
            {"type": "delegate", "from": "root", "to": "w", "prompt": "Add the endpoint. Do not modify schema.sql."},
            {"type": "tool_call", "agent": "w", "name": "edit", "args": {"path": "api.py"}},
        ])
        self.assertEqual(rep["findings"], [])

    def test_dropped_then_violated(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "root", "content": "Do not modify `schema.sql`."},
            {"type": "delegate", "from": "root", "to": "w", "prompt": "Add the endpoint."},
            {"type": "tool_call", "agent": "w", "name": "edit", "args": {"path": "schema.sql"}},
        ])
        self.assertEqual(kinds(rep), [("violated", "C1", "w"), ("dropped", "C1", "w")])
        self.assertEqual(rep["findings"][0]["evidence"]["handoff_status"], "dropped")

    def test_weakened_prohibition(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "root", "content": "Never delete `data/raw/`."},
            {"type": "delegate", "from": "root", "to": "w", "prompt": "Clean up data/raw/ to free disk space."},
        ])
        self.assertEqual(kinds(rep), [("weakened", "C1", "w")])

    def test_multi_hop_inherits(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "a", "content": "Never email `ceo@corp.com`."},
            {"type": "spawn", "parent": "a", "child": "b", "text": "Handle outreach. Never email ceo@corp.com."},
            {"type": "handoff", "from": "b", "to": "c", "prompt": "Send the outreach batch."},
            {"type": "tool", "agent": "c", "tool": "send_email", "arguments": "{\"to\": \"ceo@corp.com\"}"},
        ])
        self.assertIn(("dropped", "C1", "c"), kinds(rep))
        self.assertIn(("violated", "C1", "c"), kinds(rep))
        self.assertNotIn(("dropped", "C1", "b"), kinds(rep))

    def test_direct_violation_without_handoff(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "root", "content": "Don't run `terraform apply`."},
            {"type": "tool_call", "agent": "root", "name": "shell", "args": {"cmd": "terraform apply -auto-approve"}},
        ])
        self.assertEqual(rep["findings"][0]["evidence"]["handoff_status"], "direct")

    def test_requirement_term_overlap(self):
        rep = run([
            {"type": "message", "role": "user", "agent": "root", "content": "Always write docstrings in numpy style."},
            {"type": "delegate", "from": "root", "to": "w", "prompt": "Implement parser. Use numpy style docstrings."},
        ])
        self.assertEqual(rep["findings"], [])


class Cli(unittest.TestCase):
    def test_example_and_gate(self):
        self.assertEqual(main([EXAMPLE]), 0)
        self.assertEqual(main([EXAMPLE, "--fail-on", "high"]), 1)
        self.assertEqual(main([EXAMPLE, "--format", "json"]), 0)
        self.assertEqual(main([EXAMPLE, "--format", "md"]), 0)


if __name__ == "__main__":
    unittest.main()
