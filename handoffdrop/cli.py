import argparse
import json
import sys

from . import __version__
from .core import SEVERITY, analyze, load_trace, render_md, render_text


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="handoffdrop",
                                description="Find user constraints lost or broken across agent-to-agent handoffs.")
    p.add_argument("trace", help="JSONL/JSON trace file")
    p.add_argument("--format", choices=["text", "json", "md"], default="text")
    p.add_argument("--fail-on", choices=["low", "medium", "high", "never"], default="never",
                   help="exit 1 if any finding is at or above this severity")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args(argv)
    rep = analyze(load_trace(a.trace))
    if a.format == "json":
        print(json.dumps(rep, indent=2))
    elif a.format == "md":
        print(render_md(rep))
    else:
        print(render_text(rep))
    if a.fail_on != "never":
        lvl = SEVERITY[a.fail_on]
        if any(SEVERITY[f["severity"]] >= lvl for f in rep["findings"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
