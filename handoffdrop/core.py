"""Core analysis for handoffdrop.

A trace is a list of events (JSONL). Supported event shapes:

  {"type": "message", "role": "user", "agent": "root", "content": "..."}
  {"type": "delegate", "from": "root", "to": "coder", "prompt": "..."}
  {"type": "tool_call", "agent": "coder", "name": "write_file", "args": {...}}

Aliases are accepted: "kind" for "type", "handoff"/"spawn"/"task" for
"delegate", "text" for "content", "parent"/"child" for "from"/"to",
"tool" for "name", "arguments"/"input" for "args".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

SEVERITY = {"info": 0, "low": 1, "medium": 2, "high": 3}

NEG_RE = re.compile(
    r"\b(never|don't|dont|do not|must not|mustn't|shouldn't|should not|avoid|"
    r"no longer|without|refrain from|not allowed|forbidden|stay out of|leave .{1,40}? alone|"
    r"keep .{1,40}? untouched|under no circumstances)\b",
    re.I,
)
REQ_RE = re.compile(
    r"\b(must|always|only|make sure|ensure|required?|need to|has to|have to|stick to|use only)\b",
    re.I,
)
SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
BACKTICK = re.compile(r"`([^`]{1,120})`")
QUOTED = re.compile(r"[\"“']([^\"”']{2,80})[\"”']")
PATHLIKE = re.compile(r"(?<![\w@])(?:[\w.-]+/)+[\w.*-]*|\b[\w-]+\.(?:py|js|ts|tsx|json|ya?ml|toml|md|txt|env|cfg|ini|sql|sh|lock|csv|tex|bib|go|rs|java|rb|tf)\b")
VERSION = re.compile(r"\b[A-Za-z][\w+-]*\s?\d+(?:\.\d+)+\b")
URLISH = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)+\.(?:com|org|io|net|dev|edu)\b")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{2,}")

STOP = set("""
the and for with that this from into onto your you our are was were will would could should can
not don dont never must always only make sure ensure need have has had any all some please also
just then than them they their there here what when where which while who why how about again
avoid use using used keep leave alone untouched without stick required require task work job
agent agents sub subagent file files thing things code change changes changing edit edits editing
let get got doing done does want like over under each every other more most very much such
""".split())


@dataclass
class Constraint:
    cid: str
    text: str
    polarity: str  # "prohibit" or "require"
    anchors: list  # exact tokens (paths, backticked, versions, urls)
    terms: list  # content words
    origin_agent: str
    index: int


@dataclass
class Finding:
    kind: str
    severity: str
    constraint: str
    constraint_id: str
    agent: str
    detail: str
    event_index: int
    evidence: dict = field(default_factory=dict)


def _norm_event(e: dict) -> dict:
    t = (e.get("type") or e.get("kind") or e.get("event") or "").lower()
    if t in ("handoff", "spawn", "task", "delegation", "subagent"):
        t = "delegate"
    if t in ("tool", "call", "tool_use", "function_call"):
        t = "tool_call"
    out = {"type": t}
    if t == "message":
        out["role"] = (e.get("role") or "").lower()
        out["agent"] = e.get("agent") or e.get("to") or "root"
        c = e.get("content", e.get("text", ""))
        if isinstance(c, list):
            c = " ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
        out["content"] = str(c)
    elif t == "delegate":
        out["from"] = e.get("from") or e.get("parent") or e.get("agent") or "root"
        out["to"] = e.get("to") or e.get("child") or e.get("subagent") or "sub"
        out["prompt"] = str(e.get("prompt") or e.get("content") or e.get("text") or e.get("instructions") or "")
    elif t == "tool_call":
        out["agent"] = e.get("agent") or "root"
        out["name"] = e.get("name") or e.get("tool") or ""
        a = e.get("args", e.get("arguments", e.get("input", {})))
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except ValueError:
                pass
        out["args"] = a
    return out


def load_trace(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    s = text.lstrip()
    if s.startswith("["):
        raw = json.loads(s)
    elif s.startswith("{") and "\n{" not in s.strip():
        obj = json.loads(s)
        raw = obj.get("events", [obj]) if isinstance(obj, dict) else obj
    else:
        raw = [json.loads(l) for l in text.splitlines() if l.strip()]
    return [_norm_event(e) for e in raw if isinstance(e, dict)]


def _anchors(sentence: str) -> list:
    found = []
    for rx in (BACKTICK, URLISH, PATHLIKE, VERSION):
        for m in rx.finditer(sentence):
            tok = (m.group(1) if rx is BACKTICK else m.group(0)).strip().rstrip(".,;:")
            if len(tok) >= 2 and tok.lower() not in (a.lower() for a in found):
                found.append(tok)
    for m in QUOTED.finditer(sentence):
        tok = m.group(1).strip()
        if " " not in tok and tok.lower() not in (a.lower() for a in found):
            found.append(tok)
    # drop anchors that are substrings of longer anchors
    found = [a for a in found if not any(a != b and a.lower() in b.lower() for b in found)]
    return found


def _terms(sentence: str) -> list:
    out = []
    for w in WORD.findall(sentence):
        lw = w.lower()
        if lw in STOP or lw in out:
            continue
        out.append(lw)
    return out


def extract_constraints(text: str, origin: str, index: int, start: int = 0) -> list:
    cons = []
    for sent in SENT_SPLIT.split(text):
        sent = sent.strip(" -*\t")
        if len(sent) < 6:
            continue
        if NEG_RE.search(sent):
            pol = "prohibit"
        elif REQ_RE.search(sent):
            pol = "require"
        else:
            continue
        anchors = _anchors(sent)
        terms = _terms(sent)
        if not anchors and len(terms) < 1:
            continue
        cons.append(Constraint(f"C{start + len(cons) + 1}", sent, pol, anchors, terms, origin, index))
    return cons


def _contains(hay: str, needle: str) -> bool:
    """Case-insensitive match that respects word edges, so `main` does not hit `domain`."""
    pat = r"(?<![\w])" + re.escape(needle.lower()) + r"(?![\w])"
    return re.search(pat, hay.lower()) is not None


def coverage(c: Constraint, prompt: str) -> tuple:
    """Return (covered: bool, missing anchors, term ratio)."""
    missing = [a for a in c.anchors if not _contains(prompt, a)]
    low = prompt.lower()
    pw = set(w.lower() for w in WORD.findall(low))
    hits = [t for t in c.terms if t in pw or (len(t) > 5 and t[:5] in low)]
    ratio = len(hits) / len(c.terms) if c.terms else 1.0
    if c.anchors:
        covered = not missing
    else:
        covered = ratio >= 0.5
    return covered, missing, ratio


def _negated_near(prompt: str, anchor: str, window: int = 80) -> bool:
    low = prompt.lower()
    i = low.find(anchor.lower())
    while i != -1:
        # same sentence context around the anchor
        s = max(low.rfind(".", 0, i), low.rfind("\n", 0, i)) + 1
        seg = low[max(s, i - window): i + len(anchor) + window]
        if NEG_RE.search(seg):
            return True
        i = low.find(anchor.lower(), i + 1)
    return False


def _flatten(obj) -> str:
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False)
    return str(obj)


def analyze(events: list, min_terms: int = 1) -> dict:
    # scope[agent] = list of constraints that agent is bound by
    scope: dict = {}
    parents: dict = {}
    constraints: list = []
    passed: dict = {}  # (agent, cid) -> "full" | "dropped" | "weakened"
    findings: list = []
    delegations = 0
    counter = 0

    def bind(agent, cons):
        lst = scope.setdefault(agent, [])
        for c in cons:
            if all(c.cid != x.cid for x in lst):
                lst.append(c)

    for i, e in enumerate(events):
        t = e["type"]
        if t == "message" and e.get("role") == "user":
            new = extract_constraints(e["content"], e["agent"], i, counter)
            new = [c for c in new if c.anchors or len(c.terms) >= min_terms]
            counter += len(new)
            constraints.extend(new)
            bind(e["agent"], new)
        elif t == "delegate":
            delegations += 1
            src, dst, prompt = e["from"], e["to"], e["prompt"]
            parents[dst] = src
            inherited = []
            for c in scope.get(src, []):
                ok, missing, ratio = coverage(c, prompt)
                if not ok:
                    passed[(dst, c.cid)] = "dropped"
                    findings.append(Finding(
                        "dropped", "medium", c.text, c.cid, dst,
                        f"constraint from {c.origin_agent} not carried into handoff {src} -> {dst}"
                        + (f" (missing {', '.join(missing)})" if missing else f" (term overlap {ratio:.0%})"),
                        i, {"from": src, "to": dst, "missing_anchors": missing, "term_overlap": round(ratio, 2)}))
                    inherited.append(c)  # still binding, the child just was not told
                    continue
                if c.polarity == "prohibit" and c.anchors and not any(_negated_near(prompt, a) for a in c.anchors):
                    passed[(dst, c.cid)] = "weakened"
                    findings.append(Finding(
                        "weakened", "high", c.text, c.cid, dst,
                        f"handoff {src} -> {dst} mentions {', '.join(c.anchors)} but drops the prohibition",
                        i, {"from": src, "to": dst}))
                else:
                    passed[(dst, c.cid)] = passed.get((src, c.cid), "full") if passed.get((src, c.cid)) != "dropped" else "full"
                inherited.append(c)
            bind(dst, inherited)
            # constraints written by the delegator itself also bind the child
            own = extract_constraints(prompt, src, i, counter)
            own = [c for c in own if c.anchors and all(
                not (set(a.lower() for a in c.anchors) <= set(a.lower() for a in x.anchors)) for x in scope.get(dst, []))]
            counter += len(own)
            bind(dst, own)
        elif t == "tool_call":
            agent = e["agent"]
            blob = _flatten(e.get("args")) + " " + e.get("name", "")
            for c in scope.get(agent, []):
                if c.polarity != "prohibit" or not c.anchors:
                    continue
                hit = [a for a in c.anchors if _contains(blob, a)]
                if not hit:
                    continue
                status = passed.get((agent, c.cid), "direct")
                why = {"dropped": "after the constraint was dropped in a handoff",
                       "weakened": "after the handoff stripped the prohibition",
                       "full": "even though the handoff passed it on",
                       "direct": "against a constraint given directly to this agent"}[status]
                findings.append(Finding(
                    "violated", "high", c.text, c.cid, agent,
                    f"{e.get('name')} touched {', '.join(hit)} {why}", i,
                    {"tool": e.get("name"), "handoff_status": status}))
    findings.sort(key=lambda f: (-SEVERITY[f.severity], f.event_index))
    return {
        "events": len(events),
        "delegations": delegations,
        "constraints": [asdict(c) for c in constraints],
        "findings": [asdict(f) for f in findings],
    }


def render_text(rep: dict) -> str:
    lines = [f"handoffdrop: {rep['events']} events, {rep['delegations']} handoffs, "
             f"{len(rep['constraints'])} user constraints, {len(rep['findings'])} findings"]
    for f in rep["findings"]:
        lines.append(f"[{f['severity'].upper():6}] {f['kind']:9} {f['constraint_id']} @{f['agent']} (event {f['event_index']})")
        lines.append(f"         \"{f['constraint']}\"")
        lines.append(f"         {f['detail']}")
    if not rep["findings"]:
        lines.append("all user constraints survived every handoff")
    return "\n".join(lines)


def render_md(rep: dict) -> str:
    out = ["# handoffdrop report", "",
           f"{rep['events']} events, {rep['delegations']} handoffs, {len(rep['constraints'])} user constraints.", "",
           "| Severity | Kind | Constraint | Agent | Detail |", "|---|---|---|---|---|"]
    for f in rep["findings"]:
        out.append(f"| {f['severity']} | {f['kind']} | {f['constraint'].replace('|', '/')} | {f['agent']} | {f['detail'].replace('|', '/')} |")
    return "\n".join(out)
