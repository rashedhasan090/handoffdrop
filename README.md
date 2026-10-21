# handoffdrop

**Find the user constraints that get lost when AI agents delegate to sub-agents.**

You tell the lead agent "never touch `config/prod.yaml`" and "don't push to `main`". The lead writes a task prompt for a coder sub-agent, the coder writes one for a tester, and somewhere along the chain your rule quietly disappears. Then the tester edits `config/prod.yaml`, and every agent in the chain can honestly say nobody told it not to.

`handoffdrop` reads a multi-agent trace and follows every user constraint through every handoff. It is offline, uses only the Python standard library, and needs no model calls.

## What's novel

Most agent guardrails check one agent's tool calls against a policy. They don't check whether your instructions *survived delegation*. `handoffdrop` treats each handoff prompt as a lossy channel and checks it on its own terms.

- **Constraint mining.** It pulls prohibitions ("never", "don't", "avoid", "leave X alone", ...) and requirements ("only", "must", "always", ...) out of user messages, and finds their anchors: backticked items, paths, file names, versions, URLs, quoted tokens.
- **Handoff coverage.** For every `delegate` event it checks whether each constraint binding the delegator shows up in the child's prompt. It matches exact anchors with word edges, or content-word overlap when a constraint has no anchors.
- **Weakened prohibitions.** If the child prompt names the protected thing but drops the negation ("update config/prod.yaml so tests pass"), that's flagged as `weakened`, which is worse than a plain omission.
- **Transitive scope.** A dropped constraint still binds the child and its descendants, so a violation three hops down is attributed to the right handoff.
- **Violation attribution.** When a tool call's arguments touch a prohibited anchor, the finding says *why* it happened: the rule was dropped in a handoff, the handoff stripped the prohibition, it was passed on and ignored anyway, or it was given directly to that agent.

## Install

```bash
pip install git+https://github.com/rashedhasan090/handoffdrop
# or, from a clone
pip install .
```

Python 3.8+ with no dependencies. You can also run it in place with `python -m handoffdrop`.

## Usage

```bash
handoffdrop trace.jsonl                     # human-readable report
handoffdrop trace.jsonl --format json       # machine-readable
handoffdrop trace.jsonl --format md         # Markdown table for PR comments
handoffdrop trace.jsonl --fail-on high      # exit 1 for CI gates
```

### Trace format

JSONL, a JSON array, or `{"events": [...]}`. One event per line:

```json
{"type": "message", "role": "user", "agent": "lead", "content": "Never touch `config/prod.yaml`."}
{"type": "delegate", "from": "lead", "to": "coder", "prompt": "Refactor billing/ ..."}
{"type": "tool_call", "agent": "coder", "name": "edit_file", "args": {"path": "billing/invoice.py"}}
```

Common aliases work too: `kind`, `handoff`/`spawn`/`task`, `parent`/`child`, `text`, `tool`, and `arguments` (including JSON-string arguments).

## Demo

```bash
python -m handoffdrop examples/refactor_trace.jsonl
```

```
handoffdrop: 7 events, 3 handoffs, 3 user constraints, 9 findings
[HIGH  ] weakened  C1 @tester (event 3)
         "Never touch `config/prod.yaml`."
         handoff coder -> tester mentions config/prod.yaml but drops the prohibition
[HIGH  ] violated  C1 @tester (event 4)
         "Never touch `config/prod.yaml`."
         edit_file touched config/prod.yaml after the handoff stripped the prohibition
[HIGH  ] violated  C3 @releaser (event 6)
         "Do not push to `main`."
         shell touched main after the constraint was dropped in a handoff
[MEDIUM] dropped   C1 @coder (event 1)
         "Never touch `config/prod.yaml`."
         constraint from lead not carried into handoff lead -> coder (missing config/prod.yaml)
...
```

The lead passed the Python 3.11 and `main` rules to the coder but dropped the `config/prod.yaml` rule. The coder then told the tester to *edit* that file, and the tester did. Separately, the releaser was never told about `main` and pushed to it.

## Findings

| Kind | Severity | Meaning |
|---|---|---|
| `violated` | high | A tool call touched a prohibited anchor; `evidence.handoff_status` says why |
| `weakened` | high | A handoff mentions the protected item but no longer forbids it |
| `dropped` | medium | A binding constraint is missing from a handoff prompt |

## Limits

The extraction is heuristic and pattern-based. Constraints without concrete anchors are only checked for coverage, not violations. Paraphrased constraints ("keep prod settings as they are") may be reported as dropped. Read findings as leads for review, not proof.

## Tests

```bash
python -m unittest discover -s tests -t .
```

## License

MIT
