"""Drive ComfyUI end to end for MiniMax H3 relay-chain video generation.

Usage
-----
  python h3_pipeline.py plan     --spec job.json          # build + server-validate, do not run
  python h3_pipeline.py run      --spec job.json [--resume] [--no-assemble]
  python h3_pipeline.py status   --spec job.json
  python h3_pipeline.py assemble --spec job.json
  python h3_pipeline.py enhance  --spec job.json [--tile 512] [--duration N] [--max-frames N]

The job spec is JSON; see tools/job_example.json for a template. Everything is
logged to <rundir>/log.txt and state is kept in <rundir>/state.json so a failed
run can be resumed from the last finished segment.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import h3_build_chain as chain  # noqa: E402
import h3_check  # noqa: E402

HOST = "http://127.0.0.1:8188"
RUNS = Path(r"D:\codex-text\h3_runs")
COMFY_OUT = Path(r"D:\ComfyUI\output\video")
INPUT = Path(r"D:\ComfyUI\input")
WORKFLOW_DIR = Path(r"D:\ComfyUI\user\default\workflows\_runs")
FFMPEG = next((p for p in (r"D:\ffmpeg\bin\ffmpeg.exe",
                           r"D:\ComfyUI\.ext\Library\bin\ffmpeg.exe") if Path(p).exists()), "ffmpeg")

TIERS = {"fast": chain.TIERS[1], "quality": chain.TIERS[0]}


def load_skeleton():
    """Envelope keys copied from an existing workflow; falls back if it moved."""
    try:
        return json.loads(chain.SKELETON.read_text(encoding="utf-8"))
    except Exception:
        return {"id": "codex-h3-chain", "version": 0.4}


def check_built(job, doc, prompts, first_index, label):
    """Refuse to submit a graph whose prompts are not the ones we meant to render.

    The self-check validates the spec; this validates the built graph. They are
    different objects, and a spec/graph mismatch (empty prompts[] falling back
    to the placeholder template) once rendered 10 segments with no script.
    """
    ok, problems = h3_check.verify_graph_prompts(doc, prompts, first_index)
    if ok:
        return True
    job.log("构建校验失败（%s）：图里的提示词与 spec 不一致：" % label)
    for p in problems[:12]:
        job.log("   %s" % p)
    job.log("已拒绝提交。通常是 spec 里 prompts 没传进图（缺 prompts[] 或 "
            "shots[]），或段号与全局编号对不上。")
    return False


def http_json(path, payload=None, timeout=180):
    if payload is None:
        req = urllib.request.Request(HOST + path)
    else:
        req = urllib.request.Request(HOST + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


class Job:
    def __init__(self, spec_path):
        self.spec_path = Path(spec_path)
        self.spec = json.loads(self.spec_path.read_text(encoding="utf-8"))
        # Derive prompts[] from shots[] before anything reads spec['prompts'].
        # Without this a spec carrying only shots[] renders the placeholder
        # template instead of the real prompts.
        h3_check.normalize_spec(self.spec)
        self.name = re.sub(r"[^0-9A-Za-z_一-龥-]", "_", self.spec["name"])
        self.dir = RUNS / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.log_path = self.dir / "log.txt"
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8"))
                      if self.state_path.exists() else {})

    # ---------- logging / state ----------
    def log(self, message):
        line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def save_state(self, **kw):
        self.state.update(kw)
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    # ---------- segments ----------
    @property
    def segments(self):
        return int(self.spec["segments"])

    def segment_file(self, index):
        matches = sorted(COMFY_OUT.glob("%s_seg_%02d_*.mp4" % (self.name, index)))
        return matches[-1] if matches else None

    def done_segments(self):
        return [i for i in range(1, self.segments + 1) if self.segment_file(i)]

    # ---------- build ----------
    def build(self, start_image, prompts, prefix, out_name):
        skeleton = load_skeleton()
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        out = WORKFLOW_DIR / out_name
        _, doc = chain.build(self.segments, TIERS[self.spec.get("tier", "fast")], skeleton,
                             start_image=start_image, prompts=prompts,
                             mp=float(self.spec.get("megapixels", 0.5)),
                             frames=int(self.spec.get("segment_frames", chain.FRAMES)),
                             prefix=prefix, out_path=out, note=False,
                             last_image=self.spec.get("end_image") or None)
        shutil.copy2(out, self.dir / out_name)
        return out, doc

    def validate(self, doc):
        """Submit for validation, then pull it back before it can burn GPU time.

        ComfyUI has no dry-run endpoint, so the only way to get a real input
        check (missing model files, bad widget values) is to queue the graph.
        Deleting it from the queue is not always fast enough - if the executor
        has already picked it up, the delete is a no-op and the whole chain
        starts rendering. So: delete, then confirm nothing of ours is running
        and interrupt it if it slipped through.
        """
        info = http_json("/object_info")
        if "_error" in info:
            return False, "无法读取 /object_info：%s" % info["_error"]
        links = {l[0]: l for l in doc["links"]}
        prompt = {}
        for node in doc["nodes"]:
            ct = node["type"]
            if ct not in info:
                continue
            spec, order = {}, []
            for section in ("required", "optional"):
                for name, s in (info[ct]["input"].get(section) or {}).items():
                    spec[name] = s
                    order.append(name)
            inputs, vi = {}, 0
            widgets = [n for n in order if chain.Graph and is_widget(spec[n])]
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
        try:
            res = http_json("/prompt", {"prompt": prompt, "client_id": "codex-pipeline-validate"})
        except urllib.error.HTTPError as exc:
            return False, exc.read().decode("utf-8", "replace")[:1500]
        pid = res.get("prompt_id")
        if not pid:
            return False, json.dumps(res)[:1500]

        http_json("/queue", {"delete": [pid]})
        for _ in range(8):
            q = http_json("/queue")
            for item in q.get("queue_running") or []:
                extra = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
                if extra.get("client_id") == "codex-pipeline-validate":
                    http_json("/interrupt", {})
                    return True, "%s（校验触发了执行，已中断）" % pid
            pending = q.get("queue_pending") or []
            if not any(str(p[1]) == pid for p in pending if len(p) > 1):
                break
            time.sleep(0.4)
        return True, pid


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
    return t in {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"} or t.startswith("COMFY_")


def cmd_plan(job):
    job.log("PLAN: 构建 %d 段接力链" % job.segments)
    src = job.spec.get("start_image", chain.START_IMAGE)
    prompts = list(job.spec.get("prompts") or [])
    wf, doc = job.build(src, prompts, "%s_seg" % job.name,
                        "%s_workflow.json" % job.name)
    if not check_built(job, doc, prompts, 1, "plan"):
        return 1
    ok, info = job.validate(doc)
    if ok:
        job.log("PLAN: 服务端校验通过（%s），未执行" % info)
        return 0
    job.log("PLAN: 校验失败 -> %s" % info)
    return 1


def cmd_run(job, resume=False, assemble=True, force_queue=False, skip_check=False):
    q = http_json("/queue")
    busy = len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
    if busy and not force_queue:
        job.log("队列里还有 %d 个任务在跑/排队；ComfyUI 一次只执行一个 prompt，"
                "现在提交只会让这条长链排在后面。确认空出来再跑，或用 --force-queue 强制排队。" % busy)
        return 3

    start_image = job.spec.get("start_image", chain.START_IMAGE)
    prompts = list(job.spec.get("prompts") or [])
    first_segment = 1

    if resume:
        done = job.done_segments()
        if not done:
            job.log("RESUME: 没有已完成的片段，按全新任务处理")
        else:
            last = max(done)
            if last >= job.segments:
                job.log("RESUME: %d 段已全部完成，直接进入拼接" % job.segments)
                return assemble_output(job) if assemble else 0
            first_segment = last + 1
            last_file = job.segment_file(last)
            frame = job.dir / ("resume_from_seg%02d.png" % last)
            subprocess.run([FFMPEG, "-y", "-sseof", "-0.2", "-i", str(last_file),
                            "-frames:v", "1", str(frame)],
                           capture_output=True, text=True)
            if not frame.exists():
                job.log("RESUME: 从第 %d 段抽末帧失败，无法续跑" % last)
                return 1
            shutil.copy2(frame, INPUT / frame.name)
            start_image = frame.name
            prompts = prompts[last:]
            job.log("RESUME: 已从第 %d 段末帧续跑，剩余 %d 段" % (last, job.segments - last))

    # Rule-layer self-check on the segments that will actually render. On a
    # resume that is only the tail, so an already-rendered shot cannot block it.
    if not skip_check:
        probe = dict(job.spec)
        if first_segment > 1:
            tail = (job.spec.get("shots") or [])[first_segment - 1:]
            if tail:
                probe["shots"] = tail
            else:
                # no per-shot rows in this spec; check the sliced prompt list
                probe.pop("shots", None)
            probe["prompts"] = prompts
        files = [p.name for p in INPUT.iterdir() if p.is_file()] if INPUT.exists() else []
        report = h3_check.check_spec(probe, files)
        scope = "待渲染的 %d 段" % len(prompts) if first_segment > 1 else "全部 %d 段" % job.segments
        if report["blocked"]:
            job.log("自检未通过，已阻止提交（%s，%d 项阻断）：" % (scope, report["counts"]["blocks"]))
            for b in report["blocks"]:
                job.log("  [阻断] %s：%s" % (b["scope"], b["msg"]))
            for w in report["warns"]:
                job.log("  [提醒] %s：%s" % (w["scope"], w["msg"]))
            job.log("确认可以接受时用 --skip-check 强制提交。")
            return 2
        if report["warns"]:
            job.log("自检通过（%s），%d 项提醒：" % (scope, report["counts"]["warns"]))
            for w in report["warns"]:
                job.log("  [提醒] %s：%s" % (w["scope"], w["msg"]))

    if first_segment == 1:
        wf, doc = job.build(start_image, prompts, "%s_seg" % job.name,
                            "%s_workflow.json" % job.name)
    else:
        # build a shorter chain for the remaining segments, keeping the global numbering
        job.log("RESUME: 使用 %d 段子链（全局编号 %d 起）" % (len(prompts), first_segment))
        wf, doc = build_tail(job, start_image, prompts, first_segment)

    if not check_built(job, doc, prompts, first_segment, "run"):
        return 1

    ok, info = job.validate(doc)
    if not ok:
        job.log("提交前校验失败 -> %s" % info)
        return 1
    job.log("校验通过（临时 prompt %s），开始正式提交" % info)

    res = http_json("/prompt", {"prompt": to_api(doc), "client_id": "codex-pipeline"})
    pid = res.get("prompt_id")
    job.save_state(prompt_id=pid, started=time.time(), first_segment=first_segment)
    job.log("已提交 prompt_id=%s，开始监督" % pid)

    started = time.time()
    rc = watch(job, pid, started)
    if rc != 0:
        return rc

    job.save_state(finished=time.time(), done=job.done_segments())
    if assemble:
        return assemble_output(job)
    return 0


def cmd_watch(job, assemble=True):
    """Re-attach to a prompt that is already running.

    The supervising process can die (a workbench restart can take it with it)
    while ComfyUI keeps rendering. This picks the prompt_id back up from
    state.json, resumes progress logging, and assembles when it finishes.
    """
    pid = job.state.get("prompt_id")
    if not pid:
        job.log("WATCH: state.json 里没有 prompt_id，无法接管。")
        return 1
    hist = http_json("/history/%s" % pid)
    if isinstance(hist, dict) and pid in hist:
        job.log("WATCH: prompt %s 已经结束，直接进入拼接。" % pid)
    else:
        job.log("WATCH: 接管监督 prompt_id=%s，当前已完成 %d/%d 段"
                % (pid, len(job.done_segments()), job.segments))
    started = float(job.state.get("started") or time.time())
    rc = watch(job, pid, started)
    if rc != 0:
        return rc
    job.save_state(finished=time.time(), done=job.done_segments())
    if assemble:
        return assemble_output(job)
    return 0


def cmd_regen(job, start_from, only=False, assemble=True, force_queue=False, skip_check=False):
    """Re-render from segment N onward, or just segment N.

    `only` re-renders a single segment from the previous segment's last frame.
    Its own last frame then changes, so segments N+1.. keep their old first
    frame and the joint no longer matches - the caller is warned about that.
    """
    total = job.segments
    if not (1 <= start_from <= total):
        job.log("REGEN: 段号 %s 超出范围 1..%d" % (start_from, total))
        return 1

    q = http_json("/queue")
    busy = len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
    if busy and not force_queue:
        job.log("队列里还有 %d 个任务在跑/排队；等空出来再重跑，或用 --force-queue。" % busy)
        return 3

    if start_from == 1:
        start_image = job.spec.get("start_image", chain.START_IMAGE)
    else:
        if not job.segment_file(start_from - 1):
            job.log("REGEN: 第 %d 段还没有成片，无法从它的末帧接起。" % (start_from - 1))
            return 1
        start_image = extract_last_frame(job, start_from - 1, job.name)
        if not start_image:
            job.log("REGEN: 从第 %d 段抽末帧失败。" % (start_from - 1))
            return 1
        job.log("REGEN: 首帧取自第 %d 段的末帧（%s）" % (start_from - 1, start_image))

    prompts = list(job.spec.get("prompts") or [])
    if only:
        tail = prompts[start_from - 1:start_from]
        scope = "仅第 %d 段" % start_from
    else:
        tail = prompts[start_from - 1:]
        scope = "第 %d 段及其后 %d 段" % (start_from, total - start_from + 1)

    if not skip_check:
        probe = dict(job.spec)
        shots = (job.spec.get("shots") or [])[start_from - 1:(start_from if only else None)]
        if shots:
            probe["shots"] = shots
        else:
            probe.pop("shots", None)
        probe["prompts"] = tail
        files = [p.name for p in INPUT.iterdir() if p.is_file()] if INPUT.exists() else []
        report = h3_check.check_spec(probe, files)
        if report["blocked"]:
            job.log("REGEN: 自检未通过，已阻止重跑（%d 项阻断）：" % report["counts"]["blocks"])
            for b in report["blocks"]:
                job.log("  [阻断] %s：%s" % (b["scope"], b["msg"]))
            job.log("确认可以接受时用 --skip-check 强制重跑。")
            return 2

    reroll = int(job.state.get("reroll") or 0) + 1
    seed_base = 10000 + reroll * 1000          # a reroll must not repeat the same noise
    tag = "regen%02d_%s" % (start_from, "only" if only else "tail")
    # the end image only belongs on the chain's final segment
    reaches_end = (not only) or (start_from == total)
    wf, doc = build_range(job, start_image, tail, start_from, tag=tag, seed_base=seed_base,
                          last_image=(job.spec.get("end_image") or None) if reaches_end else None)

    if not check_built(job, doc, tail, start_from, "regen"):
        return 1

    ok, info = job.validate(doc)
    if not ok:
        job.log("REGEN: 提交前校验失败 -> %s" % info)
        return 1

    res = http_json("/prompt", {"prompt": to_api(doc), "client_id": "codex-pipeline"})
    pid = res.get("prompt_id")
    job.save_state(prompt_id=pid, started=time.time(), reroll=reroll,
                   regen_from=start_from, regen_only=bool(only))
    job.log("REGEN: 重跑%s（seed 基准 %d），已提交 prompt_id=%s"
            % (scope, seed_base, pid))
    if only:
        job.log("REGEN: 提醒——只重跑这一段会换掉它的末帧，"
                "第 %d 段的首帧仍是旧末帧，接缝处会有跳变。" % (start_from + 1))

    rc = watch(job, pid, time.time())
    if rc != 0:
        return rc
    job.save_state(finished=time.time(), done=job.done_segments())
    if assemble:
        return assemble_output(job)
    return 0


def hhmmss(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def to_api(doc):
    """UI graph -> API prompt (widgets by positional order, links by id)."""
    info = http_json("/object_info")
    links = {l[0]: l for l in doc["links"]}
    prompt = {}
    for node in doc["nodes"]:
        ct = node["type"]
        if ct not in info:
            continue
        spec, order = {}, []
        for section in ("required", "optional"):
            for name, s in (info[ct]["input"].get(section) or {}).items():
                spec[name] = s
                order.append(name)
        inputs, vi = {}, 0
        values = node.get("widgets_values") or []
        for name in [n for n in order if is_widget(spec[n])]:
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


def build_range(job, start_image, prompts, first_segment, tag="tail", seed_base=10000,
                last_image=None):
    """Chain covering only segments first_segment..N, numbered globally.

    The global numbering is load-bearing: without first_index a tail chain
    writes <name>_seg_01.. and overwrites the segments that are already good.
    """
    skeleton = load_skeleton()
    tmp = dict(job.spec)
    tmp["segments"] = len(prompts)
    saved = job.spec
    job.spec = tmp
    try:
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        out = WORKFLOW_DIR / ("%s_workflow_%s.json" % (job.name, tag))
        _, doc = chain.build(len(prompts), TIERS[saved.get("tier", "fast")], skeleton,
                             start_image=start_image, prompts=prompts,
                             mp=float(saved.get("megapixels", 0.5)),
                             frames=int(saved.get("segment_frames", chain.FRAMES)),
                             prefix="%s_seg" % job.name, out_path=out, note=False,
                             first_index=first_segment, seed_base=seed_base,
                             last_image=last_image)
    finally:
        job.spec = saved
    return out, doc


def build_tail(job, start_image, prompts, first_segment):
    # a resume always runs through to the end, so the end image still applies
    return build_range(job, start_image, prompts, first_segment, tag="resume",
                       last_image=job.spec.get("end_image") or None)


def extract_last_frame(job, segment_index, tag):
    """Pull the final frame of a finished segment into D:\\ComfyUI\\input."""
    seg = job.segment_file(segment_index)
    if not seg:
        return None
    frame = job.dir / ("%s_from_seg%02d.png" % (tag, segment_index))
    subprocess.run([FFMPEG, "-y", "-sseof", "-0.2", "-i", str(seg),
                    "-frames:v", "1", str(frame)],
                   capture_output=True, text=True)
    if not frame.exists():
        return None
    shutil.copy2(frame, INPUT / frame.name)
    return frame.name


def watch(job, pid, started):
    """Poll one submitted prompt until it leaves the history, logging progress."""
    last_report = None
    while True:
        time.sleep(20)
        elapsed = time.time() - started
        hist = http_json("/history/%s" % pid)
        done = job.done_segments()
        if pid in hist:
            status = (hist[pid].get("status") or {}).get("status_str")
            job.log("任务结束：%s，已完成 %d/%d 段，用时 %s"
                    % (status, len(done), job.segments, hhmmss(elapsed)))
            if status != "success":
                for msg in (hist[pid].get("status") or {}).get("messages") or []:
                    job.log("   %s" % (msg,))
                job.log("失败位置：第 %s 段。可用「续跑未完成段」或「重跑第 N 段」继续。"
                        % (len(done) + 1))
                return 1
            return 0
        report = "已完成 %d/%d 段，已跑 %s" % (len(done), job.segments, hhmmss(elapsed))
        if report != last_report:
            job.log("  " + report)
            last_report = report


def assemble_output(job):
    done = job.done_segments()
    if len(done) != job.segments:
        job.log("拼接中止：只有 %d/%d 段" % (len(done), job.segments))
        return 1
    files = [str(job.segment_file(i)) for i in range(1, job.segments + 1)]
    out = COMFY_OUT / ("%s_full_%ds.mp4" % (job.name, job.segments * 2))
    crossfade = float(job.spec.get("crossfade", 0) or 0)
    if crossfade <= 0:
        listfile = job.dir / "concat.txt"
        listfile.write_text("".join("file '%s'\n" % f.replace("\\", "/") for f in files),
                            encoding="utf-8")
        cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-c", "copy", str(out)]
    else:
        # Do NOT acrossfade the audio: the video is hard-concatenated, so an
        # audio-only crossfade removes `d` seconds per seam and the sound ends
        # up ~1.4s ahead of the picture over 9 seams (measured). Keep both
        # streams the same length and just dip each segment's own edges so the
        # joins do not click.
        seg_sec = float(job.spec.get("segment_frames", chain.FRAMES)) / float(chain.FPS)
        d = max(0.01, min(crossfade, seg_sec / 2.0))
        args = []
        for f in files:
            args += ["-i", f]
        n = len(files)
        graph = "".join("[%d:v]" % i for i in range(n)) + "concat=n=%d:v=1:a=0[v];" % n
        labels = []
        for i in range(n):
            graph += ("[%d:a]afade=t=in:st=0:d=%.3f,afade=t=out:st=%.3f:d=%.3f[a%d];"
                      % (i, d, seg_sec - d, d, i))
            labels.append("[a%d]" % i)
        graph += "".join(labels) + "concat=n=%d:v=0:a=1[a]" % n
        cmd = [FFMPEG, "-y"] + args + ["-filter_complex", graph,
               "-map", "[v]", "-map", "[a]", str(out)]
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0:
            job.log("ffmpeg 拼接失败：%s" % run.stderr[-400:])
            return 1
        cmd = None
    if cmd:
        run = subprocess.run(cmd, capture_output=True, text=True)
        if run.returncode != 0:
            job.log("ffmpeg 拼接失败：%s" % run.stderr[-400:])
            return 1
    job.log("成片：%s（%d 段，%s 秒素材，交叉淡化=%s）"
            % (out, job.segments, job.segments * 2, crossfade or "无"))
    job.save_state(output=str(out))
    return 0


def cmd_enhance(job, out=None, tile=512, interp=2, duration=None, max_frames=0):
    """Post-process the assembled cut: interpolate, upscale, re-encode.

    This is slow by nature (~3.5 s per output frame on this GPU), so the
    workbench launches it detached exactly like a render. Frames are cached
    under the output folder, so an interrupted run can be resumed.
    """
    src = job.state.get("output")
    if src and not Path(src).exists():
        src = None
    if not src:
        done = job.done_segments()
        if len(done) == job.segments:
            if assemble_output(job) != 0:
                return 1
            src = job.state.get("output")
    if not src or not Path(src).exists():
        job.log("增强中止：还没有可用成片，先 run 或 assemble")
        return 1
    script = Path(__file__).with_name("enhance_video.py")
    if not script.exists():
        job.log("增强中止：找不到 %s" % script)
        return 1
    dst = Path(out) if out else Path(src).with_name(Path(src).stem + "_enhanced.mp4")
    cmd = [sys.executable, "-X", "utf8", "-B", str(script), "--in", str(src),
           "--out", str(dst), "--tile", str(tile), "--interp", str(interp)]
    if duration:
        cmd += ["--duration", str(duration)]
    if max_frames:
        cmd += ["--max-frames", str(max_frames)]
    job.log("增强：%s -> %s（tile=%s，插帧 x%s）" % (src, dst, tile, interp))
    # inherit stdio so progress lands in the console / ui_run.log as it happens
    run = subprocess.run(cmd)
    if run.returncode != 0:
        job.log("增强失败（退出码 %d）" % run.returncode)
        return 1
    job.log("增强成片：%s" % dst)
    return 0


def cmd_status(job):
    q = http_json("/queue")
    print("队列: running=%d pending=%d" % (len(q.get("queue_running") or []),
                                          len(q.get("queue_pending") or [])))
    print("已完成片段: %s / 共 %d 段" % (job.done_segments() or "无", job.segments))
    if job.state.get("prompt_id"):
        print("最近一次提交: %s（开始于 %s）"
              % (job.state["prompt_id"], time.strftime("%m-%d %H:%M:%S",
                                                       time.localtime(job.state.get("started", 0)))))
    hist = http_json("/history?max_items=5")
    for pid, entry in list(hist.items())[-5:]:
        st = (entry.get("status") or {}).get("status_str")
        print("  history %s: %s" % (pid[:8], st))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["plan", "run", "status", "assemble", "regen", "watch",
                                        "enhance"])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--from", dest="from_seg", type=int, default=1,
                    help="regen: first segment to re-render (1-based)")
    ap.add_argument("--only", action="store_true",
                    help="regen: re-render only that segment, not the tail")
    ap.add_argument("--no-assemble", action="store_true")
    ap.add_argument("--force-queue", action="store_true",
                    help="submit even if another prompt is already running/queued")
    ap.add_argument("--skip-check", action="store_true",
                    help="submit even if the rule-layer self-check blocks")
    ap.add_argument("--tile", type=int, default=512,
                    help="enhance: upscale tile size; lower it if VRAM runs out")
    ap.add_argument("--interp", type=int, default=2, choices=[1, 2, 3, 4],
                    help="enhance: frame multiplier (1 disables interpolation)")
    ap.add_argument("--out", help="enhance: output path")
    ap.add_argument("--duration", type=float, help="enhance: only this many seconds")
    ap.add_argument("--max-frames", type=int, default=0,
                    help="enhance: cap output frames (preview)")
    args = ap.parse_args()

    job = Job(args.spec)
    # the workbench passes the run's own spec.json, so src and dst can coincide
    target = job.dir / "spec.json"
    if job.spec_path.resolve() != target.resolve():
        shutil.copy2(job.spec_path, target)
    if args.command == "plan":
        return cmd_plan(job)
    if args.command == "run":
        return cmd_run(job, resume=args.resume, assemble=not args.no_assemble,
                       force_queue=args.force_queue, skip_check=args.skip_check)
    if args.command == "regen":
        return cmd_regen(job, args.from_seg, only=args.only,
                         assemble=not args.no_assemble, force_queue=args.force_queue,
                         skip_check=args.skip_check)
    if args.command == "status":
        return cmd_status(job)
    if args.command == "watch":
        return cmd_watch(job, assemble=not args.no_assemble)
    if args.command == "enhance":
        return cmd_enhance(job, out=args.out, tile=args.tile, interp=args.interp,
                           duration=args.duration, max_frames=args.max_frames)
    return assemble_output(job)


if __name__ == "__main__":
    sys.exit(main())
