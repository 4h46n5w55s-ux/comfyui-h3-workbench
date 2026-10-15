"""Structurally verify hand-built ComfyUI workflow files against /object_info."""

import json
import sys
import urllib.request
from pathlib import Path

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


def is_widget(spec):
    if not isinstance(spec, list) or not spec:
        return False
    t = spec[0]
    if isinstance(t, list):
        return True
    if not isinstance(t, str):
        return False
    if len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("forceInput"):
        return False
    return t in WIDGET_TYPES or t.startswith("COMFY_")


def check(path, info):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    nodes = {n["id"]: n for n in doc["nodes"]}
    links = {l[0]: l for l in doc["links"]}

    for node in doc["nodes"]:
        ct = node["type"]
        if ct == "MarkdownNote":
            continue
        cls = info.get(ct)
        if cls is None:
            problems.append("node %s: unknown class %s" % (node["id"], ct))
            continue

        spec, order = {}, []
        for section in ("required", "optional"):
            for name, s in (cls["input"].get(section) or {}).items():
                spec[name] = s
                order.append(name)
        widgets = [n for n in order if is_widget(spec[n])]

        for inp in node.get("inputs") or []:
            name = inp["name"]
            if name not in spec:
                problems.append("node %s (%s): input %r does not exist" % (node["id"], ct, name))
                continue
            declared = inp.get("type")
            actual = spec[name][0]
            if isinstance(actual, list):
                actual = "COMBO"
            if declared != actual:
                problems.append("node %s (%s): input %s type %s != %s"
                                % (node["id"], ct, name, declared, actual))

        values = node.get("widgets_values")
        if isinstance(values, list):
            # the frontend appends an auxiliary widget after a seed
            # (control_after_generate) and after LoadImage.image (the upload button)
            aux = 0
            for w in widgets:
                cfg = spec[w][1] if len(spec[w]) > 1 and isinstance(spec[w][1], dict) else {}
                if cfg.get("control_after_generate"):
                    aux += 1
            if ct == "LoadImage":
                aux += 1
            if len(values) != len(widgets) + aux:
                problems.append("node %s (%s): %d widget values vs %d widget inputs (+%d aux) (%s)"
                                % (node["id"], ct, len(values), len(widgets), aux, ",".join(widgets)))

        for idx, out in enumerate(node.get("outputs") or []):
            if idx >= len(cls["output"]):
                problems.append("node %s (%s): output slot %d beyond class outputs" % (node["id"], ct, idx))
                continue
            if out.get("type") != cls["output"][idx]:
                problems.append("node %s (%s): output %s type %s != %s"
                                % (node["id"], ct, out.get("name"), out.get("type"), cls["output"][idx]))
            for lid in out.get("links") or []:
                if lid not in links:
                    problems.append("node %s (%s): dangling output link %s" % (node["id"], ct, lid))

        for inp in node.get("inputs") or []:
            lid = inp.get("link")
            if lid is None:
                continue
            if lid not in links:
                problems.append("node %s (%s): input %s references missing link %s"
                                % (node["id"], ct, inp["name"], lid))

    for lid, link in links.items():
        _, src, src_slot, dst, dst_slot, typ = link[:6]
        if src not in nodes:
            problems.append("link %s: source node %s missing" % (lid, src))
            continue
        if dst not in nodes:
            problems.append("link %s: target node %s missing" % (lid, dst))
            continue
        src_node, dst_node = nodes[src], nodes[dst]
        if src_slot >= len(src_node.get("outputs") or []):
            problems.append("link %s: source slot %s out of range on node %s" % (lid, src_slot, src))
        if dst_slot >= len(dst_node.get("inputs") or []):
            problems.append("link %s: target slot %s out of range on node %s" % (lid, dst_slot, dst))
        elif dst_node["inputs"][dst_slot].get("link") != lid:
            problems.append("link %s: target node %s input %s does not point back"
                            % (lid, dst, dst_node["inputs"][dst_slot]["name"]))

    return problems


def main():
    with urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=120) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    total = 0
    for path in sys.argv[1:]:
        problems = check(path, info)
        total += len(problems)
        print("=== %s : %d 个问题 ===" % (Path(path).name, len(problems)))
        for p in problems[:25]:
            print("   -", p)
    print("合计问题:", total)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
