---
name: comfyui-h3-workbench
description: Write MiniMax H3 video prompts (T2VA, I2VA, FL2VA, L2VA, Ref2VA) and drive this machine's ComfyUI to render them as continuous relay-chained segments - structure the prompt and reference mapping, build the chain workflow, submit and supervise it, resume a failed segment, and assemble the final MP4. Use for H3 prompt craft or hands-on H3 video generation here, not for general ComfyUI questions or other models.
metadata:
  short-description: H3 prompt craft plus supervised segmented video generation
---

# ComfyUI H3 Video Workbench

Run the whole job end to end: write the prompt, render it, deliver the cut. The two halves always
travel together, and a prompt written here is meant to feed the relay chain that follows.

## Phase 1 - write the prompt

Confirm the mode first: T2VA, I2VA, FL2VA, L2VA or Ref2VA. If the platform's mode definition is
unclear, ask the user to paste the current interface text - never invent capability the model has
not been shown to have.

Label every input (`@image1`, `@video1`, `@audio1`) and state what each one controls **and what it
does not control**. A reference without a stated role is a defect.

Write the prompt in five sections:

```text
[模式与素材]   mode, duration, aspect, each labelled reference and its scope
[整体声景]     ambience, music, dialogue, silence
[画面与动作]   start state -> timed beats -> camera -> end state
[时序与关键帧] keyframes, sync points, reference beats
[一致性与排除] identity/wardrobe/space/lighting to hold, and the real risks to forbid
```

Keep action, camera and sound on one timeline. Give every beat a real start and end state, because
the relay chain feeds each segment's final frame into the next segment's first frame. When the piece
is multi-segment, end on a stable, exportable frame instead of a fade or a cut to black. Time every micro-action down to sub-seconds and state normal 1x speed in the text itself - the Pacing section below is the single biggest lever on how fast the finished clip reads.

Full methodology: `references/prompt-guide.md`. Per-mode inputs and checks: `references/mode-matrix.md`.

### Writing profiles

Pick one profile per request and say which you used. All of them respect the same precedence:
official grammar > verified asset facts > the user's explicit constraints > any heuristic in these files.

| Profile | Read | When to use |
|---|---|---|
| Concise (default) | `references/prompt-guide.md` | ordinary prompts, fastest path |
| Official-rigorous | the official MiniMax H3 prompt-writing guide for T2VA/I2VA/FL2VA/L2VA and Ref2VA, plus `references/templates/`. That guide is **not** bundled here - obtain it from MiniMax | the official grammar is a hard requirement, or the prompt goes to an API |
| Chinese-friendly | `references/prompt-guide.md` | the user writes and reads Chinese; this profile sets the output language, it is not a separate grammar source |
| Creative-enhanced | `references/creative-showcase.md` (+ `references/base-multishot-format.md`, `references/ref2va-format.md` for format) | wants cinematic detail, lighting and shot design |
| Long-form planning | `references/shot-choice-library.md` + `references/director-rules.md` | 20-30 second pieces that need shot-by-shot structure before chaining |

### Pacing - applies to every profile

Physical action is the single thing that most often ruins an otherwise correct prompt, so treat this
as a hard rule rather than advice: the timing has to be written into the beats themselves.

1. **Sub-second timestamp per micro-action.** One verb spread across a multi-second window gets
   padded - the model honours the timeline, not the implied speed, and fills the leftover time with
   drift and posing. Split it:
   `2.0-2.4s B throws the right straight / 2.4-2.7s A blocks with the left forearm / 2.7-3.2s A slips around the stump / 3.2-5.0s they reset and circle`
2. **Normal speed in the positive text.** Forbidding slow motion in the exclusion list does not set a
   speed. Say "normal 1x speed, explosive release, the strike lands inside half a beat" where the
   action is described, and keep 极缓 / 缓慢 / 平滑推进 / 只有一个主运动 out of action beats - those
   are cinematic-restraint words and they read as "go slow".
3. **Event density.** Every ~3 seconds of screen time needs 3-4 discrete events.

Measured evidence (a real 15s render: mean motion 7.95, all the energy in two impact spikes while the
beat that should have been the most athletic measured lower than the punch), the avoidance-word list,
and the reusable ffmpeg motion-energy check - which doubles as a post-render gate - are in
`references/pacing-rules.md`. Read it when a shot contains physical action.
Keep the profile's output language consistent - Chinese-friendly means Chinese body text; the official profiles default to English with Chinese only where the user asks for it.
## Phase 2 - render it

Collect before building anything:

1. **Start image** - a filename already in `D:\ComfyUI\input`.
2. **Segment beats** - one beat per segment, in the user's own words. Never invent story.
3. **Tier** - `fast` (4-step turbo LoRA, ~8 min/segment) or `quality` (8-step LoRA, ~15 min/segment).
4. **Segment length** - `segment_frames` must sit on H3's `17k+5` grid. The model is only trained on
   **124-362 frames (5.17-15.08s)**, so treat that as the working range and flag anything below 124 as
   out-of-spec rather than picking it silently.
5. **Segment count** - not a fixed ladder. Total runtime is count x length, and the count follows the
   shot list; 4 segments of 124 frames is ~21s.
6. **Resolution** - 0.5 MP (960x544) default; 0.98 MP (1344x768) is the native ceiling at roughly double
   the time; 0.4 MP and 0.2 MP are also available.

Run `plan` first - it builds and server-validates the chain without rendering. Show the segment table
with the estimated wall clock, and submit only after the user agrees. Before any render, the
rule-layer self-check below must pass; a blocked job must not be submitted. While it runs, report
only meaningful transitions: a segment finished, a failure, the final cut. Do not narrate every poll.

```bash
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py plan     --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py run      --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py run      --spec <job.json> --resume
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py status   --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py assemble --spec <job.json>
```

Start from `scripts/job_example.json`. Runs last for hours: launch `run` as a background session and
poll it instead of blocking one call. `run` refuses to submit while another prompt is executing;
use `--force-queue` only deliberately. After a failure, `run --resume` extracts the last frame of the
last finished segment and chains the remainder, so finished work is never re-rendered.

`run` also refuses to submit a job that fails the rule-layer self-check below (exit code 2, reasons
in the log); `--skip-check` overrides that deliberately. On a resume only the tail is checked, so a
shot that has already been rendered can never block the remainder.

## Visual workbench

`scripts/h3_workbench_server.py` serves a local UI on http://127.0.0.1:8189. Start it with
`scripts/start_h3_workbench.bat` (there is also a desktop shortcut `H3 工作台`, hotkey `Ctrl+Alt+H`).

Reach for it when the user wants to click rather than chat - above all when they have many assets
and picking the right start frame matters. The centre pane is a **shots table**, one row per
segment (timecode / 画面节拍 / 镜头 / 声音 / 参考素材 / 自检) with that segment's full prompt on the
row directly beneath. Bind references per segment: click the `+` in a row's 参考素材 cell to arm it,
then click the material wall (or drag a thumbnail onto the row). The wall shows a `used-in-shot`
badge plus 全部 / 未用 / 已用 filters and a search box, so a large asset set stays navigable.
Right-click a thumbnail to open it full size; upload or drag files in to write them into
`D:\ComfyUI\input`. Also included: the plan / run / resume / assemble actions, live queue state,
per-segment progress chips, the run log, and an inline player for the finished cut.

### Self-check gate

保存, 预检 and 开始生成 all run the same rule-layer self-check (`scripts/h3_check.py`), evaluated
against the current spec and the real `D:\ComfyUI\input` listing. **Red items disable 开始生成 and
续跑未完成段, and the server itself refuses `run` even when the UI is stale** - a blocked job can
never start a multi-hour GPU run. Yellow items are advisory and do not block.

Blocking: empty prompt · no start frame to open the chain · timecodes not strictly increasing ·
more than 4 references on one shot (ref2va's cap) · no sub-second action timestamps · normal 1x
speed never stated.

Advisory: only one sub-second mark · a referenced file missing from `input` · references bound but
no `<Picture N>` tag in the prompt · a continuation shot that never says it carries on from the
previous frame · the same material reused across non-adjacent shots.

Audit a job without opening the UI:

```bash
D:\ComfyUI\python\python.exe <skill>\scripts\h3_check.py --spec <job.json> --inputs D:\ComfyUI\input
```

It drives the same `h3_pipeline.py` and the same `D:\codex-text\h3_runs\<name>\` job folders, so a
job started in the UI can be continued from chat and vice versa. Thumbnails are cached under
`h3_runs\_thumbs`. Only one instance may run - a second start exits immediately with a
port-busy message.
## Environment this skill assumes

- ComfyUI 0.31.0 at `D:\ComfyUI`, python at `D:\ComfyUI\python\python.exe`, HTTP API on `http://127.0.0.1:8188`.
- RTX 3050 8GB / 16GB RAM. The int8 19.5GB model is streamed every step, so throughput - not quality knobs - is the binding constraint.
- Launch with `D:\ComfyUI\run_ComfyUI_h3_sage.bat` (SageAttention + no pinned memory). Do not use `run_ComfyUI_medvram.bat`.
- ffmpeg 9.0.2 static at `D:\ffmpeg\bin\ffmpeg.exe` (also on the user PATH).
- H3 weights under `D:\ComfyUI\models`: `diffusion_models\MiniMax` (fl2va/ref2va, int8 and int4), `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq`, `vae\MiniMax`, `loras` (8-step and 4-step turbo).
- Workflows live under `D:\ComfyUI\user\default\workflows`; run state and logs go to `D:\codex-text\h3_runs\<name>\`.

## Non-obvious constraints

- Segment length is a knob, not a fixed 2-second slot: `length` must sit on H3's `17k+5` grid, and the model is trained on 124-362 frames (5.17-15.08s at 24 fps). 56 frames (2.33s) sits below that range - it still renders and it is the cheapest end of the performance curve, so it is a legitimate pick when total wall clock matters more than per-segment fidelity. Choose it deliberately and say which length was chosen; do not present it as the default.
- Cutting 56-frame segments to 48 frames in the edit gives exact 2.0s units.
- `fl2va` (first/last frame) and `ref2va` (reference images) are different nodes with different weights, so exact shared-frame chaining and reference-image locking cannot both apply to one segment. The relay chain uses `fl2va`; identity continuity comes from each segment starting on the previous segment's last frame. Ref2VA prompts therefore need their own rendering route if the user wants reference locking.
- Adjacent segments share one frame, so concatenating without trimming leaves a one-frame hold.
- Segment audio is generated jointly and concatenated by the graph, but ComfyUI has no audio crossfade node - seams are hard cuts. Either apply ffmpeg `acrossfade` afterwards or discard segment audio and lay one continuous track.
- The text encoder runs on CPU and is ~15GB; RAM, not VRAM, is what usually breaks a long chain. Read `references/operations.md` before raising resolution, segment length, or count.

## References

- `references/prompt-guide.md` - full prompt-writing methodology and quality gates.
- `references/mode-matrix.md` - per-mode inputs, prompt focus, reference priority, output checks.
- `references/operations.md` - measured throughput, timing model, job-spec fields, failure playbook.

MiniMax's own H3 prompt-writing guide is deliberately **not** redistributed in this repository.
Obtain it from MiniMax itself and read it from the user's machine when the Official-rigorous
profile is in use; do not substitute a remembered or paraphrased copy for the real guide.
