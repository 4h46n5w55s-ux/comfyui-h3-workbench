# Pacing rules (measured)

Two rules that came out of measuring a real 15-second H3 render. When a shot contains physical
action, these matter more than any descriptive adjective.

## 1. Cut micro-actions down to sub-second timestamps

One verb spread across a 3-second window gets padded. The model honours the timeline labels, not
the implied speed, and fills the unused time with slow drift and posing.

```text
bad:    2.0-5.0s  The first exchange: B steps in and throws a right straight; A slips and blocks.
better: 2.0-2.4s  B throws the right straight.
        2.4-2.7s  A blocks with the left forearm.
        2.7-3.2s  A slides around the stump.
        3.2-5.0s  They reset and circle.
```

## 2. State normal speed in the positive description

Forbidding "slow motion" in the exclusion list does not set a speed. Say the speed where the action
is described, not only among the negatives.

- say: "normal 1x speed, explosive release, the strike lands inside half a beat"
- avoid inside action beats: 极缓 / 缓慢 / 平滑推进 / 只有一个主运动 / 极短的真实微震 - these are
  cinematic-restraint words and they read as "go slow"
- raise event density: three seconds should contain three or four discrete events

## Why: the measurement

Per-frame motion energy (mean luma difference between consecutive frames, ffmpeg tblend + signalstats)
of a 15s / 960x544 render whose beats were written with 3-4 second windows:

| second | mean | peak | scripted beat |
|---|---|---|---|
| 0 | 3.41 | 7.3 | static establishing beat |
| 1-3 | 7.3-9.7 | 12.7 | circling |
| 4 | 15.64 | 19.9 | first exchange - highest sustained motion |
| 5-8 | 7.3-12.2 | 18.4 | the most athletic beat, yet lower than the punch |
| 9 | 8.71 | 55.46 | impact spike |
| 11 | 4.37 | 8.4 | mid-beat, nearly static |
| 14 | 2.60 | 5.1 | settling |

Whole clip: mean 7.95, peak 55.46. Motion lived in two impact spikes; everything else drifted.
That is the signature of a diluted time budget - not a frame-rate problem, since the file was
362 frames at 24 fps = 15.083 s, exactly as requested.

## Measuring it yourself (also usable as a post-render check)

```powershell
ffmpeg -hide_banner -loglevel error -i clip.mp4 `
  -vf "tblend=all_mode=difference,signalstats,metadata=print:file=-" -f null NUL
```

Average the `lavfi.signalstats.YAVG` values per 24 frames (one second). Rule of thumb on this
material: sustained beats above ~12 read as active, 4-8 read as drifting, below 3 read as static.
A beat whose mean sits under ~5 while its text promised fast action means the time budget was
too generous.

## Related

- 2-second segments dilute far less than one 15-second clip, because each segment carries a single
  action - but write the sub-second timestamps inside the segment all the same.
- Last resort: a uniform 1.2-1.5x retime in the edit fixes an over-slow clip, at the cost of
  smoothness and audio pitch.