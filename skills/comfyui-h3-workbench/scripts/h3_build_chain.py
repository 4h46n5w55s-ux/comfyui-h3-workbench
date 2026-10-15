"""Build the MiniMax H3 first/last-frame relay chain workflows.

Variants: 2s x 5 (10s), 2s x 10 (20s), 2s x 15 (30s).
Tiers:    8-step quality (8-step turbo LoRA) and 4-step fast (official 4-step LoRA).

Each chain: segment N's decoded last frame feeds segment N+1's first_frame, the
10/15 segment audio tracks are concatenated, and one optional whole-clip
SaveVideo (bypassed by default) emits the full piece.
"""

import json
import sys
from pathlib import Path

WF = Path(r"D:\ComfyUI\user\default\workflows")
SKELETON = WF / "MiniMax_H3_8步画质_2秒_960x544.json"

FRAMES = 56          # 2s at 24fps snapped to the model's 17k+5 grid -> 2.33s
TRIM = 48            # frames kept per segment when cutting to a true 2.0s
FPS = 24.0
MP = 0.5

UNET = r"MiniMax\minimax_h3_fl2va_pruned_int8_convrot.safetensors"
VVAE = r"MiniMax\minimax_h3_video_vae_fp16.safetensors"
AVAE = r"MiniMax\minimax_h3_audio_vae_fp32.safetensors"
LORA8 = "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors"
LORA4 = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
TE = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
START_IMAGE = "东方蓝衣仙女国风版640.310(1).png"

SEGMENT_COUNTS = [5, 10, 15]
TIERS = [
    dict(suffix="画质8步", lora=LORA8, steps=8, per=15),
    dict(suffix="快速4步", lora=LORA4, steps=4, per=8),
]

SPECS = {
    "UNETLoader": ([], [("MODEL", "MODEL")]),
    "LoraLoaderModelOnly": ([("model", "MODEL")], [("MODEL", "MODEL")]),
    "CLIPLoader": ([], [("CLIP", "CLIP")]),
    "VAELoader": ([], [("VAE", "VAE")]),
    "ResolutionSelector": ([], [("width", "INT"), ("height", "INT")]),
    "LoadImage": ([], [("IMAGE", "IMAGE")]),
    "MiniMaxH3ImageToVideo": (
        [("clip", "CLIP"), ("vae", "VAE"), ("first_frame", "IMAGE"), ("last_frame", "IMAGE"),
         ("prompt", "STRING", True), ("width", "INT", True), ("height", "INT", True), ("length", "INT", True)],
        [("positive", "CONDITIONING"), ("Latent", "LATENT")],
    ),
    "BasicGuider": ([("model", "MODEL"), ("conditioning", "CONDITIONING")], [("GUIDER", "GUIDER")]),
    "BasicScheduler": ([("model", "MODEL"), ("steps", "INT", True)], [("SIGMAS", "SIGMAS")]),
    "KSamplerSelect": ([], [("SAMPLER", "SAMPLER")]),
    "RandomNoise": ([], [("NOISE", "NOISE")]),
    "SamplerCustomAdvanced": (
        [("noise", "NOISE"), ("guider", "GUIDER"), ("sampler", "SAMPLER"),
         ("sigmas", "SIGMAS"), ("latent_image", "LATENT")],
        [("output", "LATENT"), ("denoised_output", "LATENT")],
    ),
    "VAEDecode": ([("samples", "LATENT"), ("vae", "VAE")], [("IMAGE", "IMAGE")]),
    "VAEDecodeAudio": ([("samples", "LATENT"), ("vae", "VAE")], [("AUDIO", "AUDIO")]),
    "CreateVideo": ([("images", "IMAGE"), ("audio", "AUDIO")], [("VIDEO", "VIDEO")]),
    "SaveVideo": ([("video", "VIDEO")], [("video", "VIDEO")]),
    "ImageFromBatch": ([("image", "IMAGE"), ("batch_index", "INT", True), ("length", "INT", True)],
                       [("IMAGE", "IMAGE")]),
    "ImageBatch": ([("image1", "IMAGE"), ("image2", "IMAGE")], [("IMAGE", "IMAGE")]),
    "AudioConcat": ([("audio1", "AUDIO"), ("audio2", "AUDIO"), ("direction", "COMBO", True)],
                    [("AUDIO", "AUDIO")]),
    "MarkdownNote": ([], []),
}


class Graph:
    def __init__(self):
        self.nodes, self.links, self._lid = [], [], 1

    def add(self, nid, ntype, pos, widgets=None, mode=0, title=None):
        inputs, outputs = SPECS[ntype]
        node = {
            "id": nid, "type": ntype, "pos": list(pos), "size": [280, 110],
            "flags": {}, "order": 0, "mode": mode, "inputs": [], "outputs": [],
            "properties": {"Node name for S&R": ntype},
        }
        for spec in inputs:
            entry = {"name": spec[0], "type": spec[1], "link": None}
            if len(spec) > 2 and spec[2]:
                entry["widget"] = {"name": spec[0]}
            node["inputs"].append(entry)
        for name, typ in outputs:
            node["outputs"].append({"name": name, "type": typ, "links": []})
        if widgets is not None:
            node["widgets_values"] = widgets
        if title:
            node["title"] = title
        self.nodes.append(node)
        return node

    def link(self, src, slot, dst, name):
        lid = self._lid
        self._lid += 1
        typ = src["outputs"][slot]["type"]
        src["outputs"][slot]["links"].append(lid)
        for idx, inp in enumerate(dst["inputs"]):
            if inp["name"] == name:
                inp["link"] = lid
                self.links.append([lid, src["id"], slot, dst["id"], idx, typ])
                return lid
        raise KeyError("%s has no input %r" % (dst["type"], name))


def seg_prompt(i, prompts=None):
    if prompts and i - 1 < len(prompts) and prompts[i - 1].strip():
        return prompts[i - 1]
    if i == 1:
        return ("【第 1 段 · 0.0–2.3 秒】从起始参考图继续，只描写这 2 秒内发生的事，不要复述整段剧情。\n"
                "镜头：\n动作：\n声音：")
    return ("【第 %d 段 · %.1f–%.1f 秒】承接上一段的最后一帧继续，只描写这 2 秒内发生的事。\n"
            "镜头：\n动作：\n声音：" % (i, (i - 1) * FRAMES / FPS, i * FRAMES / FPS))


def note_text(segs, tier):
    raw = segs * FRAMES / FPS
    cut = segs * TRIM / FPS
    total_min = segs * tier["per"]
    hours = total_min / 60.0
    return """## MiniMax H3 · 首尾帧接力链 · {segs} × 2 秒（成片 {cut:.0f} 秒）
### {tiername}

**怎么用**
1. 最左边的 `LoadImage` 放起始参考图
2. 依次在 {segs} 个 `MiniMaxH3ImageToVideo` 里写每段提示词 —— 只写这 2 秒内的动作 / 镜头 / 声音，**不要复述整段剧情**
3. 直接 Queue，{segs} 段按顺序自动跑完，每段存一个 mp4

**接力原理（"流畅"就来自这里）**
第 N 段解码出的 56 帧里，用 `ImageFromBatch`（batch_index = 55）取出**末帧**，
直接接到第 N+1 段的 `first_frame`。相邻两段共用同一帧，接缝是同一张图的延续，不是两段拼接。

**预计耗时**：每段约 {per} 分钟，{segs} 段合计约 {hours:.1f} 小时（{total_min} 分钟）。

**必须知道的几点**
- 每段实际是 56 帧 = **2.33 秒**，{segs} 段共 {raw:.1f} 秒；剪辑时每段裁到 48 帧就是正好 {cut:.0f} 秒。
- 相邻段重复 1 帧（上一段末帧 = 下一段首帧），拼接时删掉其中一帧，否则会有一次 1 帧的停顿。
- **音频**：{segs} 段的音轨已用 `AudioConcat` 串成一条连续音轨，和拼好的视频一起喂给整条合成的 `CreateVideo`。
  分段 mp4 各自也都带着自己的音轨。节点里只能做无缝拼接，接缝处是硬切、没有交叉淡化。
- **整条合成**：把最右边旁路的 `SaveVideo`（FULL）按 `Ctrl+B` 取消旁路即可。
  它会一次性持有 {frames} 帧（约 {gb:.1f} GB 内存），16GB 机器上有风险。
- **推荐用 ffmpeg 拼接**（无损、瞬间完成；ffmpeg 已装好并在 PATH 里）：
  ```
  rem list.txt 每行: file 'D:/ComfyUI/output/video/MiniMax_H3_Chain_01_00001_.mp4'
  ffmpeg -f concat -safe 0 -i list.txt -c copy full_{cut:.0f}s.mp4

  rem 想让接缝音频交叉淡化 0.15 秒（画面仍无缝直拼）：
  ffmpeg -i 01.mp4 -i 02.mp4 -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0[v];[0:a][1:a]acrossfade=d=0.15[a]" -map "[v]" -map "[a]" pair_01_02.mp4
  ```
  实在嫌麻烦，也可以丢掉分段音轨，铺一条连续 BGM + 环境声。

**关于参考图**
本工作流用 `fl2va`（首/尾帧）权重，节点没有参考图输入口；H3 的参考图能力在另一个节点
`MiniMaxH3ReferenceToVideo`（ref2va 权重）上，而它没有 first_frame 输入 ——
**精确共享帧接力与参考图锁定二者不可兼得**。
这条链的一致性由"每段从上一段末帧继续"保证，比外挂参考图更强，因为画面是直接延续的。
如果某段人物跑偏，可以单独用 R2V 挂参考图重做那一段再替换回来。
""".format(segs=segs, cut=cut, raw=raw, tiername=tier["suffix"], per=tier["per"],
           hours=hours, total_min=total_min, frames=segs * FRAMES,
           gb=segs * FRAMES * 960 * 544 * 3 * 4 / 1024 ** 3)


RES_TABLE = {0.2: (608, 352), 0.3: (736, 416), 0.4: (864, 480), 0.5: (960, 544),
             0.7: (1152, 640), 0.98: (1344, 768)}


def build(segs, tier, skeleton, start_image=None, prompts=None, mp=None,
          frames=None, prefix=None, out_path=None, note=True,
          first_index=1, seed_base=10000, last_image=None):
    """Build a relay chain.

    first_index is the global number of the first segment in this graph. Tail
    chains (resume, single-segment regen) start at N, and both the output file
    name and the seed must follow that numbering - otherwise a tail chain
    rewrites seg_01.. and destroys the segments already rendered.
    """
    # None means "caller didn't say" -> legacy default; "" or False means the
    # caller explicitly wants text-to-video, so no first_frame is wired at all.
    if start_image is None:
        start_image = START_IMAGE
    mp = MP if mp is None else mp
    frames = FRAMES if frames is None else frames
    prefix = prefix or "MiniMax_H3_Chain"
    width, height = RES_TABLE.get(mp, (960, 544))
    g = Graph()
    unet = g.add(1, "UNETLoader", (40, 40), [UNET, "default"])
    lora = g.add(2, "LoraLoaderModelOnly", (40, 200), [tier["lora"], 1.0])
    clip = g.add(3, "CLIPLoader", (40, 360), [TE, "minimax", "default"])
    vvae = g.add(4, "VAELoader", (40, 520), [VVAE])
    avae = g.add(5, "VAELoader", (40, 660), [AVAE])
    res = g.add(6, "ResolutionSelector", (40, 800), ["16:9 (Widescreen)", mp, 32])
    start = g.add(7, "LoadImage", (40, 960), [start_image, "image"]) if start_image else None
    end = g.add(8, "LoadImage", (40, 1080), [last_image, "image"]) if last_image else None
    g.link(unet, 0, lora, "model")

    decodes, audios, prev_image = [], [], start
    for i in range(1, segs + 1):
        idx = first_index + i - 1          # global segment number
        b = 100 + i * 20
        x = 420 + (i - 1) * 330
        i2v = g.add(b, "MiniMaxH3ImageToVideo", (x, 40), [seg_prompt(i, prompts), width, height, frames],
                     title="第 %d 段提示词" % idx)
        g.link(clip, 0, i2v, "clip")
        g.link(vvae, 0, i2v, "vae")
        # first segment of a text-to-video job has no first_frame at all
        if prev_image is not None:
            g.link(prev_image, 0, i2v, "first_frame")
        # pin the chain's final frame only; per-segment last_frame is meaningless
        if end is not None and i == segs:
            g.link(end, 0, i2v, "last_frame")
        g.link(res, 0, i2v, "width")
        g.link(res, 1, i2v, "height")

        guider = g.add(b + 1, "BasicGuider", (x, 400))
        sched = g.add(b + 2, "BasicScheduler", (x, 520), ["simple", tier["steps"], 1])
        ks = g.add(b + 3, "KSamplerSelect", (x, 660), ["res_multistep"])
        noise = g.add(b + 4, "RandomNoise", (x, 780), [seed_base + idx, "fixed"])
        samp = g.add(b + 5, "SamplerCustomAdvanced", (x, 900))
        dec = g.add(b + 6, "VAEDecode", (x, 1080))
        deca = g.add(b + 7, "VAEDecodeAudio", (x, 1200))
        cv = g.add(b + 8, "CreateVideo", (x, 1320), [FPS, 8])
        sv = g.add(b + 9, "SaveVideo", (x, 1460), ["video/%s_%02d" % (prefix, idx), "auto", "auto"])

        g.link(lora, 0, guider, "model")
        g.link(i2v, 0, guider, "conditioning")
        g.link(lora, 0, sched, "model")
        g.link(noise, 0, samp, "noise")
        g.link(guider, 0, samp, "guider")
        g.link(ks, 0, samp, "sampler")
        g.link(sched, 0, samp, "sigmas")
        g.link(i2v, 1, samp, "latent_image")
        g.link(samp, 0, dec, "samples")
        g.link(vvae, 0, dec, "vae")
        g.link(samp, 0, deca, "samples")
        g.link(avae, 0, deca, "vae")
        g.link(dec, 0, cv, "images")
        g.link(deca, 0, cv, "audio")
        g.link(cv, 0, sv, "video")
        decodes.append(dec)
        audios.append(deca)

        if i < segs:
            pick = g.add(b + 10, "ImageFromBatch", (x + 160, 1080), [frames - 1, 1])
            g.link(dec, 0, pick, "image")
            prev_image = pick

    # optional whole-clip assembly (video + concatenated audio), bypassed by default
    merged = decodes[0]
    for n, dec in enumerate(decodes[1:], start=1):
        mb = g.add(900 + n, "ImageBatch", (1700 + n * 30, 1700))
        g.link(merged, 0, mb, "image1")
        g.link(dec, 0, mb, "image2")
        merged = mb
    joined = audios[0]
    for n, aud in enumerate(audios[1:], start=1):
        ac = g.add(950 + n, "AudioConcat", (1700 + n * 30, 1840), ["after"])
        g.link(joined, 0, ac, "audio1")
        g.link(aud, 0, ac, "audio2")
        joined = ac
    cvall = g.add(1100, "CreateVideo", (2100, 1820), [FPS, 8])
    g.link(merged, 0, cvall, "images")
    g.link(joined, 0, cvall, "audio")
    svall = g.add(1101, "SaveVideo", (2100, 1960),
                  ["video/%s_FULL" % prefix, "auto", "auto"], mode=4)
    g.link(cvall, 0, svall, "video")

    if note:
        n = g.add(1200, "MarkdownNote", (40, 1120), [note_text(segs, tier)])
        n["size"] = [430, 600]

    out = Path(out_path) if out_path else (WF / ("MiniMax_H3_接力_2秒x%d_%s.json" % (segs, tier["suffix"])))
    doc = {
        "id": skeleton.get("id", "codex-h3-chain"),
        "revision": 0,
        "last_node_id": max(n["id"] for n in g.nodes),
        "last_link_id": g._lid - 1,
        "nodes": g.nodes,
        "links": g.links,
        "groups": [],
        "config": {},
        "extra": {},
        "version": skeleton.get("version", 0.4),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    print("wrote %-44s nodes=%-4d links=%-4d segs=%-3d steps=%d mp=%s -> %dx%d"
          % (out.name, len(g.nodes), len(g.links), segs, tier["steps"], mp, width, height))
    return out, doc


def main():
    if not SKELETON.exists():
        print("missing skeleton:", SKELETON)
        return 1
    skeleton = json.loads(SKELETON.read_text(encoding="utf-8"))
    for segs in SEGMENT_COUNTS:
        for tier in TIERS:
            build(segs, tier, skeleton)
    return 0


if __name__ == "__main__":
    sys.exit(main())
