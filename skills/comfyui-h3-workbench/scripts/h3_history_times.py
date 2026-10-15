"""Pull per-prompt wall times out of the running ComfyUI instance history."""

import json
import time
import urllib.request


def main():
    url = "http://127.0.0.1:8188/history?max_items=100"
    with urllib.request.urlopen(url, timeout=60) as resp:
        hist = json.loads(resp.read().decode("utf-8"))

    rows = []
    for prompt_id, entry in hist.items():
        status = entry.get("status") or {}
        start = end = None
        for name, payload in status.get("messages") or []:
            if not isinstance(payload, dict):
                continue
            if name == "execution_start":
                start = payload.get("timestamp")
            elif name in ("execution_success", "execution_error", "execution_interrupted"):
                end = payload.get("timestamp")
        if start is None:
            continue
        outputs = []
        for out in (entry.get("outputs") or {}).values():
            for key in ("images", "gifs", "videos", "audio"):
                for item in out.get(key) or []:
                    outputs.append(item.get("filename"))
        rows.append((start, end, status.get("status_str"), outputs, prompt_id))

    rows.sort()
    print("%-19s %-19s %10s  %-9s %s" % ("开始", "结束", "耗时", "状态", "产物"))
    for start, end, st, outputs, pid in rows:
        t0 = time.strftime("%m-%d %H:%M:%S", time.localtime(start / 1000))
        if end:
            t1 = time.strftime("%m-%d %H:%M:%S", time.localtime(end / 1000))
            dur = (end - start) / 1000
            durs = "%d分%02d秒" % (dur // 60, dur % 60)
        else:
            t1, durs = "-", "-"
        print("%-19s %-19s %10s  %-9s %s" % (t0, t1, durs, st, ", ".join(outputs[:3])))
    print("\n共 %d 条记录" % len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
