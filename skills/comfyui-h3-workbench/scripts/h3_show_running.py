"""Describe whatever prompt is currently executing in ComfyUI."""

import json
import urllib.request


def main():
    with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=30) as resp:
        q = json.loads(resp.read().decode("utf-8"))

    running = q.get("queue_running") or []
    pending = q.get("queue_pending") or []
    print("running=%d pending=%d" % (len(running), len(pending)))

    for item in running + pending:
        # queue items look like [number, prompt_id, prompt_graph, extra, outputs]
        graph = None
        prompt_id = None
        for part in item:
            if isinstance(part, dict) and any("class_type" in v for v in part.values() if isinstance(v, dict)):
                graph = part
            elif isinstance(part, str) and len(part) == 36:
                prompt_id = part
        print("\nprompt_id:", prompt_id)
        if not graph:
            continue
        for node_id, node in graph.items():
            ct = node.get("class_type")
            ins = node.get("inputs") or {}
            if ct in ("MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo"):
                print("  %-26s prompt=%r" % (ct, str(ins.get("prompt"))[:60]))
                print("  %-26s width=%s height=%s length=%s" % ("", ins.get("width"), ins.get("height"), ins.get("length")))
            elif ct == "BasicScheduler":
                print("  %-26s scheduler=%s steps=%s denoise=%s" % (ct, ins.get("scheduler"), ins.get("steps"), ins.get("denoise")))
            elif ct == "UNETLoader":
                print("  %-26s %s" % (ct, ins.get("unet_name")))
            elif ct == "LoraLoaderModelOnly":
                print("  %-26s %s @ %s" % (ct, ins.get("lora_name"), ins.get("strength_model")))
            elif ct == "ResolutionSelector":
                print("  %-26s %s MP=%s" % (ct, ins.get("aspect_ratio"), ins.get("megapixels")))
            elif ct == "SaveVideo":
                print("  %-26s prefix=%s" % (ct, ins.get("filename_prefix")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
