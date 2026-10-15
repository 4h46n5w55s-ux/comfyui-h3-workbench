#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Low-res video post-processing: extract frames -> RIFE interpolation ->
RealESRGAN upscale + resize to target -> high-bitrate re-encode.

High frame rate and soft detail are the two weak spots of local, small-VRAM
text-to-video. This chain runs on an 8GB RTX 3050: upscaling is tiled, so VRAM
use depends on the tile size, not on the video resolution.
"""
import argparse
import glob
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

FFMPEG = next((p for p in (r"D:\ffmpeg\bin\ffmpeg.exe",
                           r"D:\ComfyUI\.ext\Library\bin\ffmpeg.exe") if Path(p).exists()), "ffmpeg")
FFPROBE = str(Path(FFMPEG).with_name("ffprobe.exe")) if Path(FFMPEG).exists() else "ffprobe"

COMFY_API = "http://127.0.0.1:8188"

# Frame interpolation is done by the standalone ncnn build: it keeps the
# interpolator off the ComfyUI graph, so it runs while ComfyUI is busy and never
# competes for the ComfyUI model cache.
RIFE_DIRS = [r"D:\codex-text\tools\vendor\rife\*",
             r"D:\ComfyUI\models\frame_interpolation\rife-ncnn-vulkan*"]

# Ordered by preference: the compact video model first, the heavier 4x model
# only when it is the only one present.
UPSCALE_CANDIDATES = ["realesr-general-x4v3.pth", "RealESRGAN_x2plus.pth",
                      "4x-UltraSharp.pth", "RealESRGAN_x4plus.pth"]


def find_rife():
    import os
    env = os.environ.get("H3_RIFE_EXE")
    if env and Path(env).exists():
        return Path(env)
    for pattern in RIFE_DIRS:
        for hit in sorted(glob.glob(str(Path(pattern) / "rife-ncnn-vulkan.exe")), reverse=True):
            return Path(hit)
    return None


def find_upscale_model(explicit=None):
    if explicit:
        return Path(explicit)
    import os
    model_dir = Path(os.environ.get("COMFY_MODELS", r"D:\ComfyUI\models")) / "upscale_models"
    for name in UPSCALE_CANDIDATES:
        if (model_dir / name).exists():
            return model_dir / name
    return model_dir / UPSCALE_CANDIDATES[0]


def warn_if_busy():
    try:
        with urllib.request.urlopen(COMFY_API + "/queue", timeout=3) as r:
            q = json.loads(r.read().decode("utf-8"))
    except Exception:
        return
    busy = len(q.get("queue_running") or []) + len(q.get("queue_pending") or [])
    if busy:
        log("WARNING: ComfyUI has %d job(s) queued/running; measured cost is ~2x while "
            "a render is sampling, and VRAM pressure can stall this run." % busy)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def run(cmd, check=True):
    """ffprobe/ffmpeg wait on stdin on Windows; always hand them a closed stdin."""
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and proc.returncode != 0:
        raise RuntimeError("command failed: %s\n%s" % (cmd[0], (proc.stderr or "")[-2000:]))
    return proc


def probe(path):
    proc = run([FFPROBE, "-hide_banner", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
                "-show_entries", "format=duration", "-of", "json", str(path)])
    data = json.loads(proc.stdout)
    st = data["streams"][0]
    num, _, den = st["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    duration = float(st.get("duration") or data.get("format", {}).get("duration") or 0)
    nb = int(st.get("nb_frames") or round(duration * fps) or 0)
    audio = run([FFPROBE, "-hide_banner", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "json", str(path)])
    return {"width": int(st["width"]), "height": int(st["height"]), "fps": fps,
            "frames": nb, "duration": duration,
            "has_audio": bool(json.loads(audio.stdout)["streams"])}


def count_frames(directory):
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.glob("*.png"))


def extract_frames(src, out_dir, start=None, duration=None):
    """PNG sequence with 08d names; fps_mode=passthrough avoids dup/dropped frames."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-fps_mode", "passthrough", "-start_number", "1", str(out_dir / "%08d.png")]
    run(cmd)
    return count_frames(out_dir)


def interpolate(frames_in, frames_out, model, mult):
    """rife-ncnn-vulkan directory mode; -n is the target frame count."""
    if frames_out.exists():
        shutil.rmtree(frames_out)
    frames_out.mkdir(parents=True, exist_ok=True)
    exe = find_rife()
    if exe is None:
        raise SystemExit("rife-ncnn-vulkan.exe not found; run scripts/setup_assets.py")
    n_in = count_frames(frames_in)
    cmd = [str(exe), "-i", str(frames_in), "-o", str(frames_out),
           "-m", str(exe.parent / model), "-n", str(n_in * mult), "-j", "2:4:4"]
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    n_out = count_frames(frames_out)
    if n_out == 0:
        raise RuntimeError("RIFE produced no frames:\n%s"
                           % (proc.stdout or proc.stderr)[-2000:])
    return n_out


def load_upscaler(path, device, half):
    import torch
    from spandrel import ModelLoader
    desc = ModelLoader().load_from_file(str(path))
    dtype = torch.float16 if half else torch.float32
    model = desc.model.to(device=device, dtype=dtype).eval()
    return desc, model, dtype


def upscale_tiled(tensor, model, scale, tile, pad, device, dtype):
    """Tile the frame, extend each tile by pad, then crop the halo back off."""
    import torch
    _, _, h, w = tensor.shape
    out = torch.empty((1, tensor.shape[1], h * scale, w * scale), dtype=torch.float32)
    for ty in range(math.ceil(h / tile)):
        for tx in range(math.ceil(w / tile)):
            x0, y0 = tx * tile, ty * tile
            x1, y1 = min(x0 + tile, w), min(y0 + tile, h)
            ix0, iy0 = max(x0 - pad, 0), max(y0 - pad, 0)
            ix1, iy1 = min(x1 + pad, w), min(y1 + pad, h)
            patch = tensor[:, :, iy0:iy1, ix0:ix1].to(device=device, dtype=dtype)
            with torch.no_grad():
                res = model(patch)
            res = (res[0] if isinstance(res, (list, tuple)) else res).float().cpu()
            ox, oy = (x0 - ix0) * scale, (y0 - iy0) * scale
            out[:, :, y0 * scale:y1 * scale, x0 * scale:x1 * scale] = \
                res[:, :, oy:oy + (y1 - y0) * scale, ox:ox + (x1 - x0) * scale]
    return out


def upscale_frames(frames_in, frames_out, model_path, target_w, target_h, tile, pad, device,
                   max_frames=0):
    import cv2
    import numpy as np
    import torch
    frames_out.mkdir(parents=True, exist_ok=True)
    desc, model, dtype = load_upscaler(model_path, device, half=(device != "cpu"))
    scale = int(desc.scale)
    files = sorted(frames_in.glob("*.png"))
    if max_frames:
        files = files[:max_frames]
    log("upscale: %d frames, %s (x%d, %s)" % (len(files), Path(model_path).name, scale, dtype))
    t0 = time.time()
    for i, f in enumerate(files, 1):
        ft = time.time()
        img = cv2.imread(str(f), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("cannot read frame: %s" % f)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0)
        out = upscale_tiled(tensor, model, scale, tile, pad, device, dtype)
        arr = out.squeeze(0).permute(1, 2, 0).numpy()
        arr = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
        if target_h:
            h, w = arr.shape[:2]
            ar = w / h
            if target_w / target_h > ar:   # target box is wider than the frame
                th, tw = target_h, int(round(target_h * ar))
            else:                          # width binds
                tw, th = target_w, int(round(target_w / ar))
            tw -= tw % 2
            th -= th % 2
            interp = cv2.INTER_AREA if (tw * th) < (w * h) else cv2.INTER_CUBIC
            arr = cv2.resize(arr, (tw, th), interpolation=interp)
        cv2.imwrite(str(frames_out / f.name), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_PNG_COMPRESSION, 3])
        if i <= 3:
            log("  frame %d: %.2fs" % (i, time.time() - ft))
        if i % 25 == 0 or i == len(files):
            done = i / max(1, len(files))
            eta = (time.time() - t0) / done * (1 - done)
            log("  %d/%d (%.0f%%, eta %.1f min)" % (i, len(files), done * 100, eta / 60))
    return scale


def encode(frames_dir, src, out, fps, has_audio, encoder, crf, preset, audio_bitrate):
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-v", "error", "-framerate", "%.6f" % fps,
           "-start_number", "1", "-i", str(frames_dir / "%08d.png"), "-i", str(src),
           "-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0"]
    if encoder == "nvenc":
        cmd += ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(crf), "-b:v", "0"]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    # passthrough keeps every interpolated frame; the default CFR pass silently
    # drops the last few when the audio stream is fractionally longer.
    cmd += ["-fps_mode", "passthrough"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-shortest"]
    cmd += [str(out)]
    run(cmd)


def main():
    ap = argparse.ArgumentParser(description="clear + smooth video post-processing")
    ap.add_argument("--in", dest="src", required=True, help="input video")
    ap.add_argument("--out", dest="dst", help="output video (default <name>_enhanced.mp4)")
    ap.add_argument("--interp", type=int, default=2, choices=[1, 2, 3, 4],
                    help="frame multiplier, 1 = no interpolation (default 2)")
    ap.add_argument("--rife-model", default="rife-v4.6", help="RIFE model directory name")
    ap.add_argument("--upscale-model",
                    help='upscale model path, or "none" to skip')
    ap.add_argument("--target-height", type=int, default=1080, help="0 = keep upscaled size")
    ap.add_argument("--target-width", type=int, default=1920)
    ap.add_argument("--tile", type=int, default=256)
    ap.add_argument("--pad", type=int, default=16)
    ap.add_argument("--max-frames", type=int, default=0, help="0 = all frames (debug/benchmark)")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--encoder", default="x264", choices=["x264", "nvenc"])
    ap.add_argument("--audio-bitrate", default="192k")
    ap.add_argument("--start", type=float)
    ap.add_argument("--duration", type=float)
    ap.add_argument("--workdir")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate frame dirs")
    ap.add_argument("--resume", action="store_true", help="reuse extracted frames")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.exists():
        raise SystemExit("input not found: %s" % src)
    dst = Path(args.dst) if args.dst else src.with_name(src.stem + "_enhanced.mp4")
    work = Path(args.workdir) if args.workdir else dst.parent / ("_enhance_" + dst.stem)
    frames_in, frames_mid, frames_up = work / "frames_in", work / "frames_mid", work / "frames_up"
    work.mkdir(parents=True, exist_ok=True)

    info = probe(src)
    log("in: %dx%d %.3ffps %.2fs %s" % (info["width"], info["height"], info["fps"],
                                        info["duration"],
                                        "audio" if info["has_audio"] else "no audio"))
    warn_if_busy()

    n_in = count_frames(frames_in) if args.resume else 0
    if n_in:
        log("reuse %d extracted frames" % n_in)
    else:
        log("extract -> %s" % frames_in)
        n_in = extract_frames(src, frames_in, args.start, args.duration)
        log("extracted %d frames" % n_in)

    if args.interp > 1:
        log("RIFE interpolate x%d -> %s" % (args.interp, frames_mid))
        n_mid = interpolate(frames_in, frames_mid, args.rife_model, args.interp)
        log("interpolated %d -> %d frames" % (n_in, n_mid))
    else:
        frames_mid = frames_in

    # Only an explicit "none" skips the stage; an unset flag means "pick the best model".
    if args.upscale_model is None or str(args.upscale_model).lower() != "none":
        model_path = find_upscale_model(args.upscale_model)
        if not model_path.exists():
            raise SystemExit("upscale model not found: %s; run scripts/setup_assets.py"
                             % model_path)
        log("upscale + resize -> %s" % frames_up)
        upscale_frames(frames_mid, frames_up, model_path,
                       args.target_width, args.target_height, args.tile, args.pad, args.device,
                       args.max_frames)
    else:
        frames_up = frames_mid

    fps = info["fps"] * args.interp
    log("encode %.3ffps -> %s" % (fps, dst))
    encode(frames_up, src, dst, fps, info["has_audio"], args.encoder, args.crf,
           args.preset, args.audio_bitrate)
    out_info = probe(dst)
    log("done: %s (%dx%d %.3ffps %.2fs)"
        % (dst, out_info["width"], out_info["height"], out_info["fps"], out_info["duration"]))

    if not args.keep_work:
        for d in {frames_in, frames_mid, frames_up}:
            if d != frames_in or not args.resume:
                shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
