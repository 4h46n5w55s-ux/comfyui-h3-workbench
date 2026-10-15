"""Hand a finished relay chain over to the next one.

Round 2 continues round 1's story, so its first frame must be round 1's last
frame. This script extracts that frame, installs it in the ComfyUI input dir,
points the next job's spec at it, and (with --start) submits the next job.

Usage
-----
  python h3_chain_next.py --from-job forest_fight_20s --to-job forest_fight_20s_p2 --status
  python h3_chain_next.py --from-job forest_fight_20s --to-job forest_fight_20s_p2 --start
"""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import h3_check  # noqa: E402

RUNS = Path(r"D:\codex-text\h3_runs")
COMFY_OUT = Path(r"D:\ComfyUI\output\video")
INPUT = Path(r"D:\ComfyUI\input")
FFMPEG = r"D:\ffmpeg\bin\ffmpeg.exe"
WORKBENCH = "http://127.0.0.1:8189"


def load_spec(name):
    p = RUNS / name / "spec.json"
    if not p.exists():
        return None
    return h3_check.normalize_spec(json.loads(p.read_text(encoding="utf-8")))


def segment_files(name):
    out = {}
    for f in COMFY_OUT.glob("%s_seg_[0-9][0-9]_*.mp4" % name):
        try:
            idx = int(f.name.split("_seg_")[1].split("_")[0])
        except (IndexError, ValueError):
            continue
        out.setdefault(idx, []).append(f)
    return {k: sorted(v)[-1] for k, v in out.items()}   # newest wins after a regen


def extract_last_frame(seg, frames, out_name):
    """Grab the exact final frame, matching what the chain itself would chain on."""
    target = INPUT / out_name
    target.unlink(missing_ok=True)
    n = max(0, int(frames) - 1)
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(seg), "-vf", "select=eq(n\\,%d)" % n,
                    "-frames:v", "1", str(target)], capture_output=True, text=True)
    if not target.exists():
        # fall back to a tail seek if the exact-frame select did not land
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-sseof", "-0.1", "-i", str(seg), "-frames:v", "1", str(target)],
                       capture_output=True, text=True)
    return target.name if target.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-job", required=True)
    ap.add_argument("--to-job", required=True)
    ap.add_argument("--status", action="store_true", help="report readiness only")
    ap.add_argument("--start", action="store_true", help="submit the next job after wiring")
    args = ap.parse_args()

    src = load_spec(args.from_job)
    dst = load_spec(args.to_job)
    if src is None:
        print("MISSING spec: %s" % args.from_job)
        return 1
    if dst is None:
        print("MISSING spec: %s" % args.to_job)
        return 1

    total = int(src.get("segments") or 0)
    files = segment_files(args.from_job)
    have = sorted(files)
    missing = [i for i in range(1, total + 1) if i not in files]
    print("%s: %d/%d 段" % (args.from_job, len(have), total))

    if args.status:
        print("  已完成段: %s" % (have or "无"))
        if missing:
            print("  缺失段  : %s" % missing)
        print("  可以交接: %s" % ("是" if not missing and total else "否"))
        return 0 if (not missing and total) else 3

    if missing or not total:
        print("源任务还没跑完（缺 %s），不交接。" % (missing or "全部"))
        return 3

    frames = int(src.get("segment_frames") or 56)
    out_name = "%s_lastframe.png" % args.to_job
    got = extract_last_frame(files[total], frames, out_name)
    if not got:
        print("抽末帧失败：%s" % files[total])
        return 1
    print("末帧: %s（来自 %s 第 %d 段）" % (got, args.from_job, total))

    dst["start_image"] = got
    if dst.get("shots"):
        dst["shots"][0]["start_frame"] = got
    target = RUNS / args.to_job / "spec.json"
    target.write_text(json.dumps(dst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已更新 %s 的起始帧" % target)

    if args.start:
        body = json.dumps({"name": args.to_job, "action": "run"}).encode()
        req = urllib.request.Request(WORKBENCH + "/api/action", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        print("提交 %s -> ok=%s" % (args.to_job, res.get("ok")))
        print("  %s" % (res.get("message") or "").strip()[:400])
        return 0 if res.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
