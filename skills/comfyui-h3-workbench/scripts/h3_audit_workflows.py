"""Audit every saved ComfyUI workflow for broken references.

Checks, per workflow:
  * JSON parse errors / empty files
  * node classes that are not registered by the running ComfyUI
  * widget values that are not among the options the server offers
    (this is how a missing checkpoint / LoRA / VAE shows up)
  * LoadImage entries pointing at files that are not in the input folder
  * duplicate graphs (same set of node types + widget values)
"""

import hashlib
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

HOST = "http://127.0.0.1:8188"
WF_ROOT = Path(r"D:\ComfyUI\user\default\workflows")
INPUT = Path(r"D:\ComfyUI\input")
OUTPUT = Path(r"D:\ComfyUI\output")
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

MODEL_NODES = {
    "CheckpointLoaderSimple", "CheckpointLoader", "UNETLoader", "VAELoader",
    "CLIPLoader", "DualCLIPLoader", "LoraLoader", "LoraLoaderModelOnly",
    "ControlNetLoader", "UpscaleModelLoader", "CLIPVisionLoader",
    "StyleModelLoader", "DiffusionModelLoader", "ModelPatchLoader",
}

# These are resolved at runtime (options depend on another widget, or come from
# a remote API), so a static option list cannot judge them.
DYNAMIC_WIDGETS = {("SaveVideo", "codec")}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def object_info():
    with urllib.request.urlopen(HOST + "/object_info", timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_widget(spec):
    if not isinstance(spec, list) or not spec:
        return False
    type_name = spec[0]
    if isinstance(type_name, list):
        return True
    if not isinstance(type_name, str):
        return False
    if len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("forceInput"):
        return False
    return type_name in WIDGET_TYPES or type_name.startswith("COMFY_")


def options_for(spec):
    if isinstance(spec[0], list):
        return list(spec[0])
    if len(spec) > 1 and isinstance(spec[1], dict):
        opts = spec[1].get("options")
        if isinstance(opts, list):
            return list(opts)
    return None


def signature(doc):
    parts = []
    for node in sorted(doc.get("nodes", []), key=lambda n: n.get("id", 0)):
        if node.get("type") == "MarkdownNote":
            continue
        parts.append(json.dumps([node.get("type"), node.get("widgets_values")], ensure_ascii=False, sort_keys=True))
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def all_nodes(doc):
    """Every node in the file, including the ones packed inside subgraphs."""
    found = list(doc.get("nodes") or [])
    for sub in (doc.get("definitions") or {}).get("subgraphs") or []:
        found.extend(all_nodes(sub))
    return found


def check_file(path, info):
    findings = []
    raw = path.read_bytes()
    if len(raw) < 64:
        return [f"文件只有 {len(raw)} 字节，内容为空或损坏"], None, None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return [f"JSON 解析失败: {exc}"], None, None

    nodes = all_nodes(doc)
    if not nodes:
        return ["没有节点（空工作流）"], doc, None

    for node in nodes:
        class_type = node.get("type")
        if class_type == "MarkdownNote":
            continue
        if isinstance(class_type, str) and UUID_RE.match(class_type):
            continue  # subgraph instance, resolved from doc["definitions"]
        if class_type not in info:
            findings.append(f"缺节点: {class_type}（自定义节点未安装/已改名）")
            continue
        spec = {}
        order = []
        for section in ("required", "optional"):
            for name, s in (info[class_type]["input"].get(section) or {}).items():
                spec[name] = s
                order.append(name)
        widgets = [n for n in order if is_widget(spec[n])]
        values = node.get("widgets_values") or []
        if not isinstance(values, list):
            continue
        value_index = 0
        for name in widgets:
            if value_index >= len(values):
                break
            config = spec[name][1] if len(spec[name]) > 1 and isinstance(spec[name][1], dict) else {}
            if name in ("image",) and class_type == "LoadImage":
                value = values[value_index]
                if isinstance(value, str) and not (INPUT / value).exists():
                    findings.append(f"输入图缺失: {value}")
                value_index += 1
                continue
            if (class_type, name) in DYNAMIC_WIDGETS:
                value_index += 1
                continue
            value = values[value_index]
            allowed = options_for(spec[name])
            if allowed is not None and isinstance(value, str) and value not in allowed:
                kind = "模型缺失" if class_type in MODEL_NODES else "取值无效"
                findings.append(f"{kind}: {class_type}.{name} = {value!r}")
            # a seed widget is followed by an extra "control after generate" entry
            value_index += 2 if config.get("control_after_generate") else 1

    bypassed = [n.get("type") for n in nodes if n.get("mode") == 4]
    return findings, doc, bypassed


def main():
    info = object_info()
    files = sorted(
        p for p in WF_ROOT.rglob("*.json")
        if p.is_file() and "_backups" not in p.parts
    )
    sigs = defaultdict(list)
    problems = 0

    print(f"扫描 {len(files)} 个文件（含备份子目录）\n")
    for path in files:
        findings, doc, bypassed = check_file(path, info)
        rel = path.relative_to(WF_ROOT)
        if findings:
            problems += 1
            print(f"[问题] {rel}")
            for item in findings:
                print(f"        - {item}")
        if doc is not None and "backup" not in str(rel).lower() and "_backups" not in str(rel):
            sigs[signature(doc)].append(str(rel))

    print(f"\n共 {problems} 个文件有问题\n")
    print("重复内容（同一套节点+参数）：")
    for sig, names in sigs.items():
        if len(names) > 1:
            print("  " + "\n  + ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
