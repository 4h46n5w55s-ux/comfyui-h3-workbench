# Operations

## Measured throughput (RTX 3050 8GB, 16GB RAM, int8 fl2va, 8-step LoRA)

| Resolution | Frames | Result |
|---|---|---|
| 0.5 MP (960x544) | 56 (2s) | 15 min total, 9:01 of sampling, ~68 s/step |
| 0.98 MP (1344x768) | 192 (8s) | 4 h 02 m (from ComfyUI /history timestamps) |
| 0.5 MP | 362 (15s) | ~4.2 h (4 of 8 steps done after 2 h) |

The same configurations at 4 steps with the official 4-step turbo LoRA take roughly half the
sampling time.

## Timing model

Per-step cost is attention-dominated, not weight-streaming dominated:

    seconds per step ~= 0.056 * frames^1.76     (at 0.5 MP)

Wall clock therefore grows faster than linearly in frame count. Per second of finished video:
2s ~7.5 min, 5s ~9 min, 8s ~11 min, 15s ~17 min. Doubling resolution roughly doubles it again.

Consequence: shorter segments are cheaper per second of finished video. A 20s piece as 10x2s costs
~1.3 h (4-step) or ~2.5 h (8-step), while a single 15s clip costs ~4.2 h. That is a cost argument,
not a quality one: weigh it against the trained range of 124-362 frames per segment rather than
picking short segments by reflex.

## Job spec fields

| Field | Meaning |
|---|---|
| `name` | Run name; also the output filename prefix. Keep it ASCII-safe. |
| `start_image` | Filename inside `D:\ComfyUI\input` used as segment 1's first frame. |
| `tier` | `fast` (4-step LoRA) or `quality` (8-step LoRA). |
| `megapixels` | Resolution selector value: 0.5 -> 960x544, 0.98 -> 1344x768, 0.2 -> 608x352. |
| `segment_frames` | Frames per segment; must be `17k+5`. The workbench offers 56-362. Trained range is 124-362 (5.17-15.08s); 56 (2.33s) is the cheap slot below it. |
| `segments` | Number of chained segments. |
| `crossfade` | Seconds of audio crossfade during assembly; 0 = plain `-c copy` concat. |
| `prompts` | One beat per segment. Shorter lists fall back to a placeholder template. |

## Failure playbook

1. `status --spec job.json` - shows queue depth, finished segments, last submission, recent history.
2. A job that leaves the queue with a non-success status is written to `<rundir>/log.txt` with its status messages. Segment files already written are kept.
3. `run --resume` - extracts the last frame of the last finished segment with ffmpeg, copies it into `D:\ComfyUI\input`, and builds a shorter chain for the remaining beats. Re-running from scratch wastes the finished segments.
4. Repeated failures at high resolution or long chains mean memory pressure, not a broken graph: lower `megapixels`, shorten the chain, or switch to the 4-step tier.
5. Validate a freshly built workflow without burning GPU time: `scripts/h3_verify_graph.py <file>` checks node/input/type/link structure against the running server; `scripts/h3_validate_prompt.py <file>` submits for server-side validation and immediately deletes the prompt from the queue. Never run the validator while a real job is queued.
6. `scripts/h3_audit_workflows.py` scans every saved workflow for missing models or uninstalled custom nodes.

## Verified facts worth not re-learning

- `--lowvram` is a no-op under dynamic VRAM in 0.31. `--use-sage-attention` measured ~2.3x on H3-scale self-attention (seq 12k, 16 heads, dim 128).
- A shipped `minimax_h3_turbo_4step.safetensors` lacked the `diffusion_model.` key prefix, so ComfyUI silently skipped all 518 tensors; the working copy is `minimaxh3\minimax_h3_turbo_4step_comfyui.safetensors`.
- The official t2v/i2v/r2v templates needed `MiniMax\`-prefixed model paths, and the R2V one needed its 20-step/4-step switch flipped to the LoRA branch.
- ComfyUI rescans model folders automatically - new weights do not need a restart. Newly installed python packages do.
- `D:\ComfyUI\extra_model_paths.yaml` carried a stale `E:\SD.4.8` block for a drive that does not exist; it was removed.
