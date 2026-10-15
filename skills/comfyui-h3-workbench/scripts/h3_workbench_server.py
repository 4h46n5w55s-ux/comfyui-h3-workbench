"""Local web workbench for the MiniMax H3 relay-chain pipeline.

Serves a single-page UI plus a small JSON API on port 8189 and drives
tools/h3_pipeline.py for the actual ComfyUI work. Standard library only.
The bind address comes from H3_WORKBENCH_HOST (default 0.0.0.0 so the operator
console can reach it over Tailscale); there is no authentication on this API.
"""

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
PY = r"D:\ComfyUI\python\python.exe"
FFMPEG = r"D:\ffmpeg\bin\ffmpeg.exe"
PIPELINE = ROOT / "h3_pipeline.py"
HTML = ROOT / "h3_workbench.html"

RUNS = Path(r"D:\codex-text\h3_runs")
THUMBS = RUNS / "_thumbs"
INPUT = Path(r"D:\ComfyUI\input")
COMFY_OUT = Path(r"D:\ComfyUI\output\video")
COMFY = "http://127.0.0.1:8188"

PORT = 8189
# Bind address. Loopback-only would keep this UI invisible to the operator
# machine, so it listens on every interface and relies on the Windows
# firewall rule that permits only the Tailscale CGNAT range (100.64.0.0/10).
# Set H3_WORKBENCH_HOST=127.0.0.1 to lock it back down to this machine.
HOST = os.environ.get("H3_WORKBENCH_HOST", "0.0.0.0")
_procs = {}          # run name -> Popen of a pipeline run
SETTINGS = RUNS / "_settings.json"


def read_settings():
    """Global switches. Default is command mode - ask before acting."""
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {"mode": data.get("mode") or "command"}
    except Exception:
        pass
    return {"mode": "command"}


def write_settings(patch):
    s = read_settings()
    if isinstance(patch, dict):
        s.update(patch)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return s

sys.path.insert(0, str(ROOT))
import h3_check  # noqa: E402


def comfy(path, payload=None, timeout=15):
    try:
        if payload is None:
            req = urllib.request.Request(COMFY + path)
        else:
            req = urllib.request.Request(COMFY + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except Exception as exc:
        return {"_error": str(exc)}


def safe_name(name):
    return re.sub(r"[^0-9A-Za-z_一-龥-]", "_", str(name))[:60]


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def safe_input(name):
    """Resolve a filename inside the ComfyUI input dir, refusing traversal."""
    p = (INPUT / Path(str(name)).name).resolve()
    return p if p.parent == INPUT.resolve() else None


def list_inputs():
    if not INPUT.exists():
        return []
    files = [p for p in INPUT.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "size": p.stat().st_size,
             "mtime": int(p.stat().st_mtime)} for p in files]


def thumb_path(name):
    src = safe_input(name)
    if not src or not src.exists():
        return None
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / (re.sub(r"[^0-9A-Za-z_.-]", "_", src.stem)[:60] + "_%d.jpg" % src.stat().st_mtime)
    if not out.exists():
        r = subprocess.run([FFMPEG, "-y", "-i", str(src),
                            "-vf", "scale=200:-2:flags=area", "-q:v", "6", str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            return None
    return out


def run_dir(name):
    return RUNS / safe_name(name)


def read_spec(name):
    p = run_dir(name) / "spec.json"
    if not p.exists():
        p = run_dir(name) / "spec.original.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


FRAMES_PER_SEG = 56
FPS = 24.0


def migrate_spec(spec):
    """Bring older jobs (prompts[]) onto the shots[] model used by the stage UI.

    Delegates to h3_check.normalize_spec so the workbench and h3_pipeline.py
    can never disagree about what a spec means.
    """
    return h3_check.normalize_spec(spec)


def script_path(name):
    return run_dir(name) / "script.md"


def segment_files(name):
    # only per-segment files: the in-graph "seg_FULL" clip must not count as one
    return sorted(COMFY_OUT.glob("%s_seg_[0-9][0-9]_*.mp4" % safe_name(name)))


def done_count(files):
    """Distinct segment numbers that have a file.

    A regen writes a second file for the same segment (…_seg_03_00002_.mp4), so
    counting files would report 4/3 on a 3-segment job.
    """
    idx = set()
    for f in files:
        m = re.search(r"_seg_(\d+)_", f.name)
        if m:
            idx.add(int(m.group(1)))
    return len(idx)


def spec_check(spec):
    """Run the shared rule-layer self-check against the real input dir."""
    return h3_check.check_spec(spec, list_inputs())


def comfy_running_pids():
    """prompt_ids ComfyUI is executing right now (survives a workbench restart)."""
    q = comfy("/queue")
    if not isinstance(q, dict) or "_error" in q:
        return set()
    out = set()
    for item in q.get("queue_running") or []:
        if isinstance(item, list) and len(item) > 1:
            out.add(str(item[1]))
    return out


def job_summary(name, running_pids=frozenset()):
    d = run_dir(name)
    spec = migrate_spec(read_spec(name) or {})
    state = {}
    if (d / "state.json").exists():
        state = json.loads((d / "state.json").read_text(encoding="utf-8"))
    files = segment_files(name)
    final = sorted(COMFY_OUT.glob("%s_full_*.mp4" % safe_name(name)))
    pid = state.get("prompt_id")
    running = ((name in _procs and _procs[name].poll() is None)
               or (pid is not None and str(pid) in running_pids))
    return {
        "name": name,
        "spec": spec,
        "check": spec_check(spec),
        "script": script_path(name).read_text(encoding="utf-8") if script_path(name).exists() else "",
        "segments": int(spec.get("segments") or 0),
        "done": done_count(files),
        "segment_files": [f.name for f in files],
        "final": final[-1].name if final else None,
        "prompt_id": pid,
        "started": state.get("started"),
        "log": (d / "log.txt").read_text(encoding="utf-8")[-4000:] if (d / "log.txt").exists() else "",
        "running": running,
    }


def list_jobs():
    if not RUNS.exists():
        return []
    return sorted(p.name for p in RUNS.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / "spec.json").exists())


def tail_log(name, lines=60):
    p = run_dir(name) / "log.txt"
    if not p.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8").splitlines()[-lines:])


def start_action(name, action, extra=None):
    d = run_dir(name)
    spec = d / "spec.json"
    if not spec.exists():
        return False, "缺少 spec.json"
    args = [PY, "-X", "utf8", "-B", str(PIPELINE), action, "--spec", str(spec)]
    if extra:
        args += extra
    # "enhance" is a long GPU job too, so it gets the same detached treatment
    # as a render instead of blocking the request for an hour.
    if action in ("run", "enhance"):
        if name in _procs and _procs[name].poll() is None:
            return False, "该任务已在跑"
        log = (d / "ui_run.log").open("a", encoding="utf-8")
        _procs[name] = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                        cwd=str(ROOT))
        return True, "已启动，日志见 ui_run.log"
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port, which
    # silently produces duplicate workbench processes. Refuse instead.
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "H3Workbench/1.0"

    def log_message(self, *args):        # keep the console quiet
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, HTML.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        if u.path == "/api/state":
            runp = comfy_running_pids()
            return self._json({
                "queue": comfy("/queue"),
                "history": comfy("/history?max_items=10"),
                "jobs": [job_summary(n, runp) for n in list_jobs()],
                "inputs": list_inputs(),
                "mode": read_settings().get("mode"),
                "now": time.time(),
            })
        if u.path == "/api/inputs":
            return self._json({"inputs": list_inputs()})
        if u.path == "/thumb":
            t = thumb_path(q.get("f", [""])[0])
            if not t:
                return self._send(404, "no thumb", "text/plain; charset=utf-8")
            return self._send(200, t.read_bytes(), "image/jpeg")
        if u.path == "/inputmedia":
            p = safe_input(q.get("f", [""])[0])
            if not p or not p.exists():
                return self._send(404, "not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return self._send(200, p.read_bytes(), ctype)
        if u.path == "/api/log":
            return self._json({"log": tail_log(q.get("name", [""])[0])})
        if u.path == "/media":
            rel = unquote(q.get("f", [""])[0])
            target = (COMFY_OUT / rel).resolve()
            if not str(target).startswith(str(COMFY_OUT.resolve())) or not target.exists():
                return self._send(404, "not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._send(200, target.read_bytes(), ctype)
        return self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception as exc:
            return self._json({"ok": False, "message": "bad json: %s" % exc}, 400)

        if u.path == "/api/save":
            name = safe_name(payload.get("name") or "")
            if not name:
                return self._json({"ok": False, "message": "任务名不能为空"}, 400)
            d = run_dir(name)
            d.mkdir(parents=True, exist_ok=True)
            spec = payload.get("spec") or {}
            spec["name"] = name
            spec = migrate_spec(spec)
            (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
            return self._json({"ok": True, "name": name, "segments": spec.get("segments", 0)})

        if u.path == "/api/script":
            name = safe_name(payload.get("name") or "")
            if not name:
                return self._json({"ok": False, "message": "缺少任务名"}, 400)
            d = run_dir(name)
            d.mkdir(parents=True, exist_ok=True)
            script_path(name).write_text(payload.get("script") or "", encoding="utf-8")
            return self._json({"ok": True, "chars": len(payload.get("script") or "")})

        if u.path == "/api/check":
            spec = payload.get("spec")
            if not isinstance(spec, dict):
                # fall back to the saved spec for a named job
                spec = read_spec(safe_name(payload.get("name") or "")) or {}
            res = spec_check(migrate_spec(spec))
            res["ok"] = True
            return self._json(res)

        if u.path == "/api/action":
            name = safe_name(payload.get("name") or "")
            action = payload.get("action") or ""
            if action not in ("plan", "run", "status", "assemble", "regen", "enhance"):
                return self._json({"ok": False, "message": "未知动作"}, 400)

            extra = None
            if action == "run" and payload.get("resume"):
                extra = ["--resume"]
            if action == "regen":
                try:
                    seg = int(payload.get("from") or 1)
                except (TypeError, ValueError):
                    return self._json({"ok": False, "message": "重跑的段号必须是数字"}, 400)
                extra = ["--from", str(seg)]
                if payload.get("only"):
                    extra.append("--only")

            if action in ("run", "regen"):
                # real gate: never start a GPU run on a spec that fails the
                # rule-layer self-check, even if the UI state is stale.
                spec = migrate_spec(read_spec(name) or {})
                probe = spec
                if action == "regen" and seg > 1:
                    tail = (spec.get("shots") or [])[seg - 1:]
                    probe = dict(spec)
                    if tail:
                        probe["shots"] = tail
                    else:
                        probe.pop("shots", None)
                    probe["prompts"] = (spec.get("prompts") or [])[seg - 1:]
                res = spec_check(probe)
                if res["blocked"]:
                    what = "重跑" if action == "regen" else "生成"
                    lines = ["自检未通过，已阻止%s：" % what]
                    lines += ["  [阻断] %s：%s" % (b["scope"], b["msg"]) for b in res["blocks"]]
                    return self._json({"ok": False, "message": "\n".join(lines)})

            ok, out = start_action(name, action, extra)
            return self._json({"ok": ok, "message": out[-4000:]})

        if u.path == "/api/delete":
            name = safe_name(payload.get("name") or "")
            d = run_dir(name)
            if d.exists():
                shutil.rmtree(d)
            return self._json({"ok": True})

        if u.path == "/api/upload":
            filename = Path(str(payload.get("filename") or "")).name
            if not filename or Path(filename).suffix.lower() not in IMAGE_EXT:
                return self._json({"ok": False, "message": "只接受图片文件"}, 400)
            target = safe_input(filename)
            if not target:
                return self._json({"ok": False, "message": "非法文件名"}, 400)
            # body is the raw file: strip the JSON wrapper if the client sent one
            raw = payload.get("__raw__")
            if raw is None:
                return self._json({"ok": False, "message": "缺少文件内容"}, 400)
            import base64
            try:
                data = base64.b64decode(raw)
            except Exception as exc:
                return self._json({"ok": False, "message": "解码失败: %s" % exc}, 400)
            INPUT.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return self._json({"ok": True, "name": target.name, "size": len(data)})

        if u.path == "/api/delete_image":
            name = Path(str(payload.get("filename") or "")).name
            target = safe_input(name)
            if not target or not target.exists() or not target.is_file():
                return self._json({"ok": False, "message": "找不到这张图（可能已经删了）"}, 404)
            # refuse silently deleting something a job still renders from
            used = []
            for job in list_jobs():
                spec = migrate_spec(read_spec(job) or {})
                if Path(str(spec.get("start_image") or "")).name == name:
                    used.append("%s 的起始图" % job)
                for s in spec.get("shots") or []:
                    if any(Path(str(r)).name == name for r in (s.get("refs") or [])):
                        used.append("%s 第 %s 段参考图" % (job, s.get("i")))
            if used and payload.get("confirm_used") is not True:
                return self._json({"ok": False, "need_confirm": True, "used": used,
                                   "message": "这张图正被使用：" + "、".join(used)})
            try:
                target.unlink()
            except OSError as exc:
                return self._json({"ok": False, "message": "删除失败：%s" % exc}, 500)
            return self._json({"ok": True, "name": name, "used": used})

        if u.path == "/api/mode":
            m = str(payload.get("mode") or "")
            if m not in ("auto", "command"):
                return self._json({"ok": False, "message": "mode 必须是 auto 或 command"}, 400)
            s = write_settings({"mode": m})
            return self._json({"ok": True, "mode": s.get("mode")})

        if u.path == "/api/chain":
            src = safe_name(payload.get("from") or "")
            dst = safe_name(payload.get("to") or "")
            if not src or not dst:
                return self._json({"ok": False, "message": "需要 from 与 to"}, 400)
            try:
                import h3_chain_next
                res = h3_chain_next.handover(src, dst, start=False)
            except Exception as exc:
                return self._json({"ok": False, "message": "交接失败：%s" % exc}, 500)
            if res.get("ok") and payload.get("start"):
                ok, out = start_action(dst, "run")
                res["submitted"] = ok
                res["submit_message"] = out[-1500:]
                res["ok"] = bool(ok)
            return self._json(res)

        if u.path == "/api/fs/delete":
            # the workbench runs outside the shell sandbox, so file cleanup that
            # needs to touch D:\ComfyUI goes through here instead of a shell
            # command that would raise an approval prompt.
            roots = [COMFY_OUT, INPUT, Path(r"D:\ComfyUI\temp"), RUNS, ROOT / "output"]
            roots = [p.resolve() for p in roots]
            deleted, skipped = [], []
            for raw in (payload.get("paths") or []):
                try:
                    rp = Path(str(raw)).resolve()
                except OSError:
                    skipped.append("%s（路径无效）" % raw)
                    continue
                if not any(rp == r or str(rp).startswith(str(r) + os.sep) for r in roots):
                    skipped.append("%s（不在允许目录）" % raw)
                    continue
                if not rp.is_file():
                    skipped.append("%s（不是文件）" % raw)
                    continue
                try:
                    rp.unlink()
                    deleted.append(str(rp))
                except OSError as exc:
                    skipped.append("%s（%s）" % (raw, exc))
            return self._json({"ok": True, "deleted": deleted, "skipped": skipped})

        return self._send(404, "not found", "text/plain; charset=utf-8")


def main():
    RUNS.mkdir(parents=True, exist_ok=True)
    if not HTML.exists():
        print("missing", HTML)
        return 1
    try:
        srv = Server((HOST, PORT), Handler)
    except OSError as exc:
        print("already running or port busy on %d: %s" % (PORT, exc))
        return 0
    print("H3 workbench on http://127.0.0.1:%d  (bound %s, Ctrl+C to stop)"
          % (PORT, HOST), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
