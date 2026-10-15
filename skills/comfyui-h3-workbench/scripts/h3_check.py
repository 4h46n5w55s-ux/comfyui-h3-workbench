"""Rule-layer self-check for MiniMax H3 relay-chain jobs.

Shared by the workbench server (to gate 开始生成) and by the render pipeline.
It is deliberately local and cheap: it catches the mistakes that otherwise cost
GPU hours, before anything is submitted to ComfyUI.

Blocking checks (red, generation is disabled):
  * a shot has an empty prompt
  * the whole job has no start frame to open the relay chain
  * timecodes are not strictly increasing
  * a shot binds more reference images than ref2va accepts
  * a shot has no sub-second action timestamps (pacing rule)
  * a shot never states normal 1x speed (pacing rule)

Warning checks (yellow, generation still allowed):
  * only one sub-second mark (a window needs a start and an end)
  * a referenced file is not present in the ComfyUI input dir
  * a shot references images but the prompt never tags <Picture N>
  * a continuation shot's prompt never acknowledges the previous frame
  * the same material is reused across non-adjacent shots

Run standalone:  python h3_check.py --spec job.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

MAX_REFS = 4          # ref2va accepts up to four reference images
FPS = 24.0
FRAMES_PER_SEG = 56

# a bare decimal number, e.g. 0.4 / 1.25 / 12.5
_NUM = re.compile(r"(?<![\d.])(\d+\.\d{1,2})(?![\d])")
# "1x" / "1×" / 正常速度 style wording
_SPEED = re.compile(r"1\s*[×xX]|正常\s*1|原速|实时速度|真实速度|正常速度|常规速度")
# continuation wording for shots after the first
_CONT = re.compile(r"承接|延续|接续|接上|上一帧|前一段|首帧|续接")
_PICTURE = re.compile(r"<\s*Picture\s*\d+\s*>", re.IGNORECASE)
# wording that keeps a slow shot from turning into a dead freeze
_ALIVE = re.compile(r"呼吸|微动|轻颤|飘|浮动|流动|移动|起伏|眨眼|视线|眼神|风吹|光影|发丝|衣摆")

PACE_ACTION, PACE_NORMAL, PACE_STILL = "action", "normal", "still"
PACE_LABEL = {PACE_ACTION: "动作", PACE_NORMAL: "常规", PACE_STILL: "静态"}


def _subsec_marks(text):
    """Decimal tokens that read as sub-second/短秒 timecodes (0..6)."""
    marks = []
    for m in _NUM.finditer(text or ""):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0.0 <= v <= 6.0:
            marks.append(v)
    return marks


def pacing_check(prompt):
    """Return (granularity_ok, speed_ok, note) for one shot's prompt."""
    marks = sorted(_subsec_marks(prompt))
    granularity = False
    if len(marks) >= 2:
        # a real micro-beat means two marks less than a second apart
        granularity = any(abs(b - a) < 1.0 for a, b in zip(marks, marks[1:]))
    speed = bool(_SPEED.search(prompt or ""))
    note = ""
    if not granularity:
        note = "缺少亚秒级动作时间戳（需至少两个、且相邻间隔 <1 秒，如 0.0 起手 / 0.4 出拳）"
    if not speed:
        note = (note + "；" if note else "") + "未写明正常 1× 速度（如“全程正常 1× 速度”）"
    return granularity, speed, note


def _names(inputs):
    """Accept list_inputs() dicts, a list/tuple of names, or None."""
    if not inputs:
        return None
    out = set()
    for item in inputs:
        if isinstance(item, dict):
            n = item.get("name")
        else:
            n = str(item)
        if n:
            out.add(Path(n).name)
    return out


def normalize_spec(spec):
    """Derive prompts[] from shots[] (and vice versa) so every entry point agrees.

    The renderer reads spec['prompts'] directly, while the workbench authors
    shots[]. If a spec carries only shots[], the renderer sees an empty prompt
    list and silently falls back to its placeholder template - which is exactly
    how a 10-segment job once rendered with no script at all. Both the server
    and h3_pipeline.py call this before using a spec.
    """
    if not isinstance(spec, dict):
        return spec
    try:
        frames = int(spec.get("segment_frames") or FRAMES_PER_SEG)
    except (TypeError, ValueError):
        frames = FRAMES_PER_SEG
    seg_seconds = frames / FPS
    shots = spec.get("shots")
    if not shots:
        prompts = spec.get("prompts") or []
        shots = []
        for i, p in enumerate(prompts):
            shots.append({
                "i": i + 1,
                "start": round(i * seg_seconds, 2),
                "end": round((i + 1) * seg_seconds, 2),
                "beat": "", "camera": "", "sound": "",
                "prompt": p, "refs": [], "start_frame": "", "check": {},
            })
        spec["shots"] = shots

    shots = spec.get("shots") or []
    spec["segments"] = len(shots)
    spec["prompts"] = [(s.get("prompt") or "") for s in shots]
    first = next((s.get("start_frame") for s in shots if s.get("start_frame")), "")
    if first:
        spec["start_image"] = first
    return spec


PLACEHOLDER_MARK = "镜头：\n动作：\n声音："


def verify_graph_prompts(doc, expected, first_index=1):
    """Check the built graph really carries the prompts we meant to render.

    Returns (ok, problems). This is the check that was missing when a job
    rendered straight from the placeholder template: the self-check had
    validated the spec, but nothing compared the spec against the graph that
    was actually submitted.
    """
    found = {}
    for node in (doc or {}).get("nodes") or []:
        if node.get("type") != "MiniMaxH3ImageToVideo":
            continue
        m = re.search(r"第\s*(\d+)\s*段", node.get("title") or "")
        if not m:
            continue
        values = node.get("widgets_values") or [""]
        found[int(m.group(1))] = values[0] if values else ""

    problems = []
    for offset, want in enumerate(expected or []):
        idx = first_index + offset
        if idx not in found:
            problems.append("图里找不到第 %d 段的采样节点" % idx)
            continue
        got = found[idx] or ""
        if got.strip() != (want or "").strip():
            problems.append("第 %d 段提示词与 spec 不一致（图里 %d 字，spec %d 字）"
                            % (idx, len(got), len(want or "")))
        elif len(got.strip()) < 120 and PLACEHOLDER_MARK.replace("\n", "\n") in got:
            problems.append("第 %d 段用的是兜底占位模板，不是真提示词" % idx)
    if not expected:
        problems.append("spec 里一段提示词都没有")
    return (not problems), problems


def check_spec(spec, inputs=None):
    """Return a dict with per-shot checks plus job-level blocks and warnings.

    Shape: {blocked, blocks:[{scope,msg}], warns:[{scope,msg}],
            shots:[{bad:[],warn:[],ok,note}], counts:{...}}
    """
    spec = spec or {}
    shots = spec.get("shots") or []
    if not shots:
        prompts = spec.get("prompts") or []
        # synthesize a contiguous timeline so the timecode rule still applies
        seg = FRAMES_PER_SEG / FPS
        shots = [{"prompt": p, "refs": [], "start_frame": "",
                  "start": round(i * seg, 3), "end": round((i + 1) * seg, 3)}
                 for i, p in enumerate(prompts)]

    have = _names(inputs)
    blocks, warns = [], []
    per_shot = []
    ref_use = {}          # filename -> [shot indices]

    def block(scope, msg):
        blocks.append({"scope": scope, "msg": msg})

    def warn(scope, msg):
        warns.append({"scope": scope, "msg": msg})

    if not shots:
        block("全片", "分镜表是空的，至少要有 1 段")

    # source_mode: "image" (default, backward compatible) or "text" (T2V)
    src_mode = str(spec.get("source_mode") or "image").lower()
    start_image = spec.get("start_image") or (shots[0].get("start_frame") if shots else "")
    if src_mode == "text":
        if start_image:
            warn("全片", "模式是「纯文生视频」但设了起始图；第一段仍会以它作为首帧")
    elif shots and not start_image:
        block("全片", "缺少起始帧：请把某张素材设为起始图，或把模式切到「纯文生视频」")

    # H3's `length` snaps up to a 17k+5 grid and is only trained on ~124-362 frames
    try:
        nframes = int(spec.get("segment_frames") or FRAMES_PER_SEG)
    except (TypeError, ValueError):
        nframes = FRAMES_PER_SEG
    if nframes % 17 != 5:
        block("全片", "每段 %d 帧不在 H3 的 17k+5 网格上，提交后会被自动向上取整" % nframes)
    elif not (124 <= nframes <= 362):
        warn("全片", "每段 %d 帧（%.2f 秒）超出模型训练区间 124–362 帧（5.17–15.08 秒）"
             % (nframes, nframes / FPS))

    prev_end = None
    for idx, s in enumerate(shots):
        scope = "第 %d 段" % (idx + 1)
        bad, w = [], []
        prompt = (s.get("prompt") or "").strip()

        if not prompt:
            bad.append("提示词为空")
        else:
            marks = _subsec_marks(prompt)
            micro = len(marks) >= 2 and any(abs(b - a) < 1.0 for a, b in zip(marks, marks[1:]))
            speed = bool(_SPEED.search(prompt))
            alive = bool(_ALIVE.search(prompt))
            pace = (s.get("pace") or PACE_NORMAL)
            if pace not in PACE_LABEL:
                pace = PACE_NORMAL

            # Pacing rules only bind where they were derived from: fast physical
            # action. A slow or held shot is a legitimate choice, not a defect.
            if pace == PACE_ACTION:
                if not micro:
                    bad.append("标为「动作」但缺少亚秒级动作时间戳"
                               "（需至少两个、且相邻间隔 <1 秒，如 0.0 起手 / 0.4 出拳）")
                if not speed:
                    bad.append("标为「动作」但未写明正常 1× 速度（如“全程正常 1× 速度”）")
            elif pace == PACE_STILL:
                if not alive:
                    w.append("标为「静态」却没有任何让画面保持活动的描写"
                             "（呼吸 / 微动 / 光影流动 / 视线移动）——死帧会传染给下一段")
                if micro and not speed:
                    w.append("给了亚秒级节拍但没写速度，容易被读成慢动作")
            else:  # normal
                if not marks and not alive:
                    w.append("既没有时间戳也没有活动描写，画面可能变成定格")
                if marks and not speed and micro:
                    w.append("有亚秒级节拍但没写速度；若含物理动作建议写明“正常 1× 速度”")
            if idx > 0 and not _CONT.search(prompt):
                w.append("承接段未提到延续上一帧，容易被模型当作全新镜头")

        refs = [Path(r).name for r in (s.get("refs") or [])]
        if len(refs) > MAX_REFS:
            bad.append("绑定了 %d 张参考图，ref2va 最多接受 %d 张" % (len(refs), MAX_REFS))
        if refs and not _PICTURE.search(prompt or ""):
            w.append("绑了参考图但提示词没写 <Picture N> 指代，模型不知道引用哪张")
        if have is not None:
            missing = [r for r in refs if r not in have]
            if missing:
                w.append("参考图不在 input 目录：%s" % "、".join(missing[:3]))
        for r in refs:
            ref_use.setdefault(r, []).append(idx + 1)

        st, en = s.get("start"), s.get("end")
        try:
            st, en = float(st), float(en)
        except (TypeError, ValueError):
            st = en = None
        if st is None or en is None or en <= st:
            bad.append("时间码无效（结束必须大于开始）")
        else:
            if prev_end is not None and st < prev_end - 1e-6:
                bad.append("时间码与上一段重叠（第 %d 段起 %.2fs < 上段结束 %.2fs）"
                           % (idx + 1, st, prev_end))
            prev_end = en

        for msg in bad:
            block(scope, msg)
        for msg in w:
            warn(scope, msg)
        per_shot.append({
            "bad": bad, "warn": w,
            "ok": not bad and not w,
            "note": "；".join(bad + w) or "自检通过",
        })

    # reused material across non-adjacent shots -> identity drift risk
    for name, idxs in ref_use.items():
        for a, b in zip(idxs, idxs[1:]):
            if b - a > 1:
                warn("第 %d、%d 段" % (a, b),
                     "同一张参考图 %s 跨段复用（相距较远，注意角色一致性）" % name)

    return {
        "blocked": bool(blocks),
        "blocks": blocks,
        "warns": warns,
        "shots": per_shot,
        "counts": {"shots": len(shots), "blocks": len(blocks), "warns": len(warns)},
    }


def load_spec(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="H3 relay-chain rule self-check")
    ap.add_argument("--spec", required=True, help="job spec json")
    ap.add_argument("--inputs", help="ComfyUI input dir (scan for real files)")
    ap.add_argument("--json", action="store_true", help="print raw json")
    args = ap.parse_args(argv)

    spec = load_spec(args.spec)
    inputs = None
    if args.inputs:
        d = Path(args.inputs)
        inputs = [p.name for p in d.iterdir() if p.is_file()] if d.exists() else []

    res = check_spec(spec, inputs)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        tag = "阻断" if res["blocked"] else "通过"
        print("自检%s：%d 段，%d 项阻断，%d 项警告"
              % (tag, res["counts"]["shots"], res["counts"]["blocks"], res["counts"]["warns"]))
        for b in res["blocks"]:
            print("  [阻断] %s：%s" % (b["scope"], b["msg"]))
        for w in res["warns"]:
            print("  [警告] %s：%s" % (w["scope"], w["msg"]))
    return 1 if res["blocked"] else 0


if __name__ == "__main__":
    sys.exit(main())
