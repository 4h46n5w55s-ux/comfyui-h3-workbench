# comfyui-h3-workbench

给 Codex / ChatGPT 用的 Agent Skill：**写 MiniMax H3 视频提示词**，并**驱动本地 ComfyUI 把提示词渲染成连续分段的成片**。

一句故事想法进来，一条 MP4 出去：先按 H3 的语法把提示词写成结构化的五段体，再把它铺成一条接力链
（每段约 2.3 秒，上一段的末帧就是下一段的首帧），提交、监督、失败续跑、最后拼接。

## 技能做什么

**阶段一 · 写提示词**

- 先定模式：`T2VA` / `I2VA` / `FL2VA` / `L2VA` / `Ref2VA`
- 每份素材标明角色：`@image1` 控制什么，以及**不**控制什么
- 按五段体写：`[模式与素材]` `[整体声景]` `[画面与动作]` `[时序与关键帧]` `[一致性与排除]`
- 动作按亚秒级时间戳切分，并在正文里显式写明 1x 正常速度

**阶段二 · 渲染**

- `plan` 先出分镜表和预计耗时，用户确认后才提交
- `run` 调 ComfyUI 逐段生成；失败用 `--resume` 从断点续跑，已完成的段不会重渲
- `assemble` 拼成最终 MP4
- 提交前强制过一遍规则层自检，**红项直接挡住生成**，不会让一个有问题的任务跑几小时 GPU

完整工作流、写作档位、节奏规则和环境假设见 [`skills/comfyui-h3-workbench/SKILL.md`](skills/comfyui-h3-workbench/SKILL.md)。

附带一个本地可视化工作台（`scripts/h3_workbench_server.py`）：分镜表格、素材墙、逐段绑定参考、
队列状态、逐段进度、运行日志和内嵌播放器。适合素材多、需要肉眼挑首帧的场景。

## 安装

**方式一 · Codex 技能安装器**

```bash
$skill-installer --repo 4h46n5w55s-ux/comfyui-h3-workbench --path skills/comfyui-h3-workbench
```

**方式二 · 手动**

```bash
git clone https://github.com/4h46n5w55s-ux/comfyui-h3-workbench.git
cp -r comfyui-h3-workbench/skills/comfyui-h3-workbench ~/.codex/skills/
```

**方式三 · 只给某个项目用**

放进项目的 `.agents/skills/` 目录，Codex 扫描仓库时会自动发现。

## 使用

装好后在 Codex 里显式点它的名字：

```text
$comfyui-h3-workbench 把这段故事写成 H3 提示词：<你的故事>
```

也可以不点名，直接描述任务，Codex 会按技能的 description 自己匹配。下面的命令都假设你已经满足
后面的「环境要求」。

### 一个最小流程

**第 1 步 · 先只写提示词，别渲染**

```text
用 $comfyui-h3-workbench 把这句想法写成 Ref2VA 提示词，先不要渲染：
一个修理工在雨夜的天台上发现一台还在运转的旧收音机
```

技能会先跟你确认模式和素材角色（`@image1` 到底控制什么），再按五段体输出。素材角色没写清楚的
时候它会追问，这是设计如此，不是卡住。

**第 2 步 · 出分镜表**

把起始帧文件放进 ComfyUI 的 `input` 目录，然后：

```text
按这个提示词规划 10 段（约 20 秒），fast 档，960x544，先 plan 给我看分镜表和预计耗时
```

`plan` 只建链并做服务端校验，**不渲染**。段数换算：5 段 ≈ 10 秒，10 段 ≈ 20 秒，15 段 ≈ 30 秒。

**第 3 步 · 确认后再渲染**

```text
确认，开始 run，跑完直接 assemble 拼成 MP4
```

渲染以小时计，`run` 会作为后台会话跑。中途失败用 `--resume` 从断点续，**已完成的段不会重渲**。
提交前会强制过一遍规则层自检，红项直接挡住，不会让有问题的任务白跑几小时 GPU。

**第 4 步 · 或者用可视化工作台**

```text
启动 H3 工作台
```

也可以双击桌面快捷方式 `H3 工作台`（热键 `Ctrl+Alt+H`），界面在 `http://127.0.0.1:8189`。
分镜表格、素材墙、逐段绑定参考都在这里，素材多、需要肉眼挑首帧时比对话方便。

### 不走对话，直接调脚本

```bash
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py plan     --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py run      --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py status   --spec <job.json>
D:\ComfyUI\python\python.exe <skill>\scripts\h3_pipeline.py assemble --spec <job.json>
```

任务清单从 `scripts/job_example.json` 起手改。想看逐条的写作规则和失败处理，读
`skills/comfyui-h3-workbench/SKILL.md` 和它旁边的 `references/`。

## 环境要求

这个技能是按**一台具体的机器**写出来的，脚本里有硬编码的 Windows 绝对路径。在别的机器上用之前，
必须先改这些路径，否则 `plan` / `run` 会直接找不到文件。

| 假定 | 说明 |
|---|---|
| `D:\ComfyUI` | ComfyUI 安装目录，含 `input/`、`output/`、`models/` |
| `D:\ComfyUI\python\python.exe` | 用来跑 `scripts/*.py` 的解释器 |
| `D:\codex-text\h3_runs\<name>\` | 运行状态、日志、历史任务的存放位置 |
| `D:\ffmpeg\bin\ffmpeg.exe` | 视频编码与拼接 |
| `http://127.0.0.1:8188` | ComfyUI 的 HTTP API |
| ComfyUI 0.31.0 | 节点名和参数按这个版本写的 |
| RTX 3050 8GB / 16GB RAM | 吞吐估算基于这个配置；换机器预估耗时会偏 |

含硬编码路径、需要按你的机器修改的文件：

```
SKILL.md
references/operations.md
scripts/enhance_video.py
scripts/h3_audit_workflows.py
scripts/h3_build_chain.py
scripts/h3_chain_next.py
scripts/h3_make_task2.py
scripts/h3_pipeline.py
scripts/h3_verify_graph.py
scripts/h3_workbench_server.py
scripts/h3_workbench.html
scripts/start_h3_workbench.bat
```

模型权重也需要自备：`MiniMax` 的 `fl2va`/`ref2va` 扩散模型、`qwen3vl_32b_minimax_h3` 文本编码器、
video/audio VAE，以及 8-step / 4-step turbo LoRA。

## 安全提示

`scripts/h3_workbench_server.py` 默认监听 `0.0.0.0:8189`，而这个界面**没有任何鉴权**——能连上就能
提交渲染、读写任务文件。原作者的部署靠 Windows 防火墙只放行单一来源地址来保护。

换到别的机器上跑之前，建议至少做一件事：

```bash
set H3_WORKBENCH_HOST=127.0.0.1
```

或者自己加一条只放行可信来源的防火墙规则。不要直接把它暴露到不可信网络。

## 关于 MiniMax 官方文档

H3 的提示词语法以 MiniMax 官方的《Video Prompt Writing Guide》为准。那份文档是 MiniMax 的版权材料，
**本仓库不分发**，请自行从 MiniMax 获取并在本机阅读。

`SKILL.md` 里的 Official-rigorous 档位需要读那份官方文档，仓库只提供指向说明，不提供副本。

## 目录结构

```
comfyui-h3-workbench/
├── README.md
├── LICENSE
├── .gitignore
└── skills/
    └── comfyui-h3-workbench/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/          写作方法论、模式矩阵、节奏规则、模板
        └── scripts/             流水线、自检、可视化工作台
```

## 许可

MIT，见 [LICENSE](LICENSE)。
