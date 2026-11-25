"""Compare what the app sends with an agent's exported JSON schema.

Muse Code's `muse schema` export looked like this (JSON Schema plus a methods
table), and checking against it found the draft's wrong method names and
fields before any real request was sent:

    {"$defs": {...}, "methods": {"turn/start": {"params": {"$ref": ...},
                                                "result": {...}}, ...}}

Usage (outside the repo; read-only):

    python -I schema_check.py SCHEMA.json SENT.json

SENT.json maps each method the app calls to the parameter names it sends:

    {"turn/start": ["sessionId", "commandId", "input"],
     "approval/listPending": ["sessionId"]}

It reports, per method: missing from the schema (watch the case), required
fields not sent, and fields sent that the schema doesn't know. A clean report
is necessary, not sufficient: probe the real agent too (SKILL.md).

If the agent's schema has another shape, change `methods()` and `params()`.
"""
import json
import sys


def resolve(node, defs, depth=0):
    while isinstance(node, dict) and "$ref" in node and depth < 8:
        node = defs[node["$ref"].split("/")[-1]]
        depth += 1
    return node


def methods(schema):
    return schema.get("methods", {})


def params(schema, method):
    entry = methods(schema)[method]
    return resolve(entry.get("params", {}), schema.get("$defs", {}))


def main(schema_path, sent_path):
    schema = json.load(open(schema_path, encoding="utf-8"))
    sent = json.load(open(sent_path, encoding="utf-8"))
    problems = 0
    for method, fields in sorted(sent.items()):
        if method not in methods(schema):
            near = [m for m in methods(schema) if m.lower() == method.lower()]
            print(f"MISSING  {method}" + (f"  (did you mean {near[0]}?)" if near else ""))
            problems += 1
            continue
        p = params(schema, method)
        known = set(p.get("properties", {}))
        required = set(p.get("required", []))
        missing = sorted(required - set(fields))
        unknown = sorted(set(fields) - known) if known else []
        status = "ok" if not (missing or unknown) else "CHECK"
        problems += bool(missing or unknown)
        print(f"{status:7}  {method}  missing-required={missing}  unknown-sent={unknown}")
    print(f"\n{problems} method(s) to check. Then probe the real agent: the schema can"
          " promise what the installed build refuses.")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))
