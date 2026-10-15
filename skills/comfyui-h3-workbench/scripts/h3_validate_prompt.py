"""Submit a UI workflow to ComfyUI for validation, then delete it from the queue.

Nothing executes: the job only lands in queue_pending and is removed again.
ComfyUI validates the whole graph on submit, so any bad input name, type or
missing model shows up as node_errors.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:8188"
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


def http_json(path, payload=None, timeout=180):
    if payload is None:
        req = urllib.request.Request(HOST + path)
    else:
        req = urllib.request.Request(HOST + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


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


def convert(doc, info):
    links = {l[0]: l for l in doc.get("links") or []}
    prompt = {}
    for node in doc["nodes"]:
        ct = node.get("type")
        if ct not in info:
            continue
        spec, order = {}, []
        for section in ("required", "optional"):
            for name, s in (info[ct]["input"].get(section) or {}).items():
                spec[name] = s
                order.append(name)
        inputs, vi = {}, 0
        widgets = [n for n in order if is_widget(spec[n])]
        values = node.get("widgets_values") or []
        for name in widgets:
            if vi >= len(values):
                break
            inputs[name] = values[vi]
            cfg = spec[name][1] if len(spec[name]) > 1 and isinstance(spec[name][1], dict) else {}
            vi += 2 if cfg.get("control_after_generate") else 1
        for slot in node.get("inputs") or []:
            link = links.get(slot.get("link"))
            if link:
                inputs[slot["name"]] = [str(link[1]), link[2]]
        prompt[str(node["id"])] = {"class_type": ct, "inputs": inputs}
    return prompt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflows", nargs="+")
    args = ap.parse_args()

    info = http_json("/object_info")
    for path in args.workflows:
        doc = json.loads(open(path, encoding="utf-8").read())
        prompt = convert(doc, info)
        print("=== %s  -> %d nodes ===" % (path.split("\\")[-1], len(prompt)))
        try:
            res = http_json("/prompt", {"prompt": prompt, "client_id": "codex-validate"})
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            print("   校验失败 HTTP %s" % exc.code)
            try:
                err = json.loads(body)
                print("   error:", json.dumps(err.get("error"), ensure_ascii=False)[:300])
                for nid, ne in list((err.get("node_errors") or {}).items())[:12]:
                    cls = ne.get("class_type")
                    for e in ne.get("errors", []):
                        print("   node %-5s %-28s %s: %s"
                              % (nid, cls, e.get("type"), e.get("details")))
            except Exception:
                print("   raw:", body[:500])
            continue

        pid = res.get("prompt_id")
        print("   校验通过，prompt_id=%s（立即从队列删除，不会执行）" % pid)
        try:
            http_json("/queue", {"delete": [pid]})
            q = http_json("/queue")
            still = [x for x in (q.get("queue_pending") or []) if pid in json.dumps(x)]
            print("   已删除:", "否，仍在队列！" if still else "是")
        except Exception as exc:
            print("   ⚠ 删除失败，请手动检查队列:", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
