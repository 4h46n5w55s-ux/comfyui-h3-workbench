# H3 模式矩阵

下表用于整理提示词结构，不替代平台界面的实时说明。若平台文字与本文冲突，以当前平台为准。

| 模式 | 主要输入 | 提示词重点 | 必须确认 |
| --- | --- | --- | --- |
| T2VA | 文字 | 画面动作、镜头、声景、对白、音乐 | 时长、画幅、音频是否原生 |
| I2VA | 图像 + 文字 | 首图锚定、从首图开始的动作、镜头和声音 | 图像控制了什么，不控制什么 |
| FL2VA | 首帧 + 尾帧 + 文字 | 起止状态、过渡路径、动作和摄影机 | 两帧的身份/空间是否一致 |
| L2VA | 末帧或末状态 + 文字 | 如何到达末状态，反向规划动作 | 末帧是否允许改变 |
| Ref2VA | 图像/视频/音频参考 + 文字 | 参考标签、冲突优先级、动作和声景映射 | 每项参考的控制范围 |

## 参考优先级模板

```text
@image1 controls identity only.
@image2 controls location and lighting only.
@video1 controls motion rhythm and camera only.
@audio1 controls music mood and beat only.
If references conflict, identity > spatial continuity > style.
```

## 输出检查

- 参考素材数量是否超出当前模式限制？
- 首帧、尾帧或参考视频的时间点是否明确？
- 对白是否写清说话人、台词、时点和画内/画外？
- 音乐是否在不该出现的时间出现？
- 动作、镜头和声音是否落在同一时间轴？
