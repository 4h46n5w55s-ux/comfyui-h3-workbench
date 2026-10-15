"""Generate the spec for the forest-fight follow-up (round 2).

Round 2 exists to test a different direction than round 1:

  * exclusions cut from 241 chars to ~55, with every body-part word removed
    (naming 手/指/肢体 in a CFG-free positive prompt can prime the defect)
  * an explicit, complete limb description added instead
    ("A 只有两条手臂：右拳在下颌前方，左拳收在胸口，两肘贴住肋侧")
  * less body-on-body contact: attacks are thrown and evaded at range, so the
    two figures touch only in the two beats that actually need it
  * four sub-second beats per 2.3s segment instead of three

The start image is left blank here on purpose: h3_chain_next.py fills it with
the last frame of round 1 once that render has finished.
"""

import json
import sys
from pathlib import Path

FRAMES_PER_SEG = 56
FPS = 24.0
SEG = FRAMES_PER_SEG / FPS

# ---- shared blocks (kept short and free of body-part negation) ----
IDENTITY = (
    "超写实真人电影实拍，森林清晨，树冠过滤的侧逆光。画面中恰好两个人。"
    "A 在屏幕左侧：年轻东亚女性，黑色高马尾，深墨绿色无袖紧身上衣，黑色高腰宽松长裤，黑色软底鞋。"
    "B 在屏幕右侧：年轻东亚男性，黑色短发，浅灰色卷袖亚麻上衣，深橄榄色宽松长裤，黑色鞋。"
    "A 恒在屏幕左侧，B 恒在屏幕右侧，动作轴线不翻转。"
)

LIMBS = (
    "A 只有两条手臂：右拳在下颌前方，左拳收在胸口，两肘自然贴住肋侧；"
    "B 只有两条手臂：右臂向前伸出，左拳护在下颌旁。"
    "两人的手各自归位、在画面里清楚可辨。"
)

REALISM = (
    "真人实拍质感：真实毛孔、细纹、肤色差异、汗珠、湿发、肌肉张力、呼吸起伏、衣物褶皱，"
    "真实自然光与景深。表演克制：表情只靠眼神、下颌与眉头的细微变化，不夸张、不瞪眼、不张嘴、不嘶吼。"
    "全程正常 1× 速度，不做慢动作。"
)

EXCLUDE = "排除：动画感、塑料皮肤；第三个人；武器；文字、字幕、水印、Logo；对白、旁白。"


def prompt(act, camera, sound, first=False):
    lead = "从起始参考图开始，不重置人物位置。" if first else "承接上一帧继续，不重置人物位置。"
    return "%s%s%s\n【本段】%s%s\n镜头：%s\n声音：%s\n%s" % (
        IDENTITY, LIMBS, REALISM, lead, act, camera, sound, EXCLUDE)


SHOTS = [
    dict(
        beat="对峙重启：两端压低重心，缓慢侧移",
        camera="低机位全景固定，轻微手持呼吸感",
        sound="鸟鸣、树叶摩擦、湿苔上的轻脚步",
        act="0.0-0.6s 两人保持上一片结尾的收势，重心继续下沉；"
            "0.6-1.4s 两人各自向自己一侧缓慢横移半步、视线锁定对方；"
            "1.4-2.3s A 的右脚跟轻轻碾地调整站位，B 的左脚向前蹭了半寸。",
    ),
    dict(
        beat="A 主动进攻：三步冲过中间空地",
        camera="侧向跟拍横移，与 A 同速，保持双人可读",
        sound="急促脚步踩碎落叶、衣料破风",
        act="0.0-0.5s A 右脚蹬地起步；0.5-1.3s A 连迈三步冲过中间空地，横向位移明显；"
            "1.3-2.3s A 在 B 前一米处急停，右拳收在下颌前。",
    ),
    dict(
        beat="A 高扫落空：B 后仰躲开",
        camera="中景侧面，轻微跟随 A 的腿部动作",
        sound="腿部破空的呼声、脚掌落地的闷响",
        act="0.0-0.5s A 抬起左腿起高扫；0.5-1.0s 左腿扫向 B 的头部高度，B 后仰闪避；"
            "1.0-1.6s A 的脚扫空、身体被带得转过半圈；1.6-2.3s A 落回正面站架。",
    ),
    dict(
        beat="B 反击扫腿：A 跳起躲过",
        camera="低机位贴近地面，扫腿轨迹从画面下方通过",
        sound="鞋底擦地、起跳与落地的闷响",
        act="0.0-0.4s B 趁机沉身低扫 A 的支撑腿；0.4-0.9s A 双脚跳起躲开；"
            "0.9-1.5s A 落地并顺势转身；1.5-2.3s 两人回到一臂半距离对峙。",
    ),
    dict(
        beat="A 绕到侧翼：环绕镜头 180 度",
        camera="围绕两人缓慢环绕半圈，始终把两人放在画面里",
        sound="脚步错动、压低呼吸",
        act="0.0-0.6s A 压低重心向屏幕后方切入；0.6-1.4s A 沿弧线绕到 B 的侧翼；"
            "1.4-2.3s B 转头跟上 A 的位置，双脚原地调整朝向。",
    ),
    dict(
        beat="B 回身格挡：前臂接住两拳",
        camera="中近景，手部动作完整留在画面内",
        sound="前臂相撞的闷响、布料摩擦",
        act="0.0-0.4s A 打出第一记直拳；0.4-0.7s B 用右前臂向外格挡；"
            "0.7-1.2s A 补出第二拳；1.2-1.8s B 侧身卸力、把 A 的力引向身侧；1.8-2.3s 两人分开半步。",
    ),
    dict(
        beat="同时后撤：拉开距离，呼吸明显",
        camera="中全景固定，镜头略微后拉",
        sound="沉重呼吸、鞋底在湿地上的摩擦",
        act="0.0-0.5s 两人同时发力分开；0.5-1.2s 各自后撤一步半；"
            "1.2-1.8s 两人胸口起伏明显、手臂稍降；1.8-2.3s 重新举拳对峙。",
    ),
    dict(
        beat="A 假动作后低位切入",
        camera="中景侧向横移跟随",
        sound="衣料破风、突然加快的脚步",
        act="0.0-0.4s A 先做一次高位假动作；0.4-0.9s A 立刻压低重心向 B 的下盘切入；"
            "0.9-1.5s B 被迫后退半步；1.5-2.3s B 的脚跟踩到湿泥、重心开始不稳。",
    ),
    dict(
        beat="分出结果：B 脚下一滑，半跪在湿地",
        camera="中景，轻微向下倾斜跟随 B 的下落",
        sound="鞋底打滑的长音、膝盖砸进湿地的闷响、泥点溅开",
        act="0.0-0.5s B 左脚在湿地上打滑；0.5-1.1s B 单膝跪进泥里、右手撑地；"
            "1.1-1.7s A 收住动作、向后撤半步保持距离；1.7-2.3s A 双拳仍在胸前，没有追击。",
    ),
    dict(
        beat="尾帧：A 立姿收势，B 半跪抬头",
        camera="固定全景，不移动",
        sound="环境声收尾，呼吸渐渐平复",
        act="0.0-0.6s A 调整呼吸、双拳停在胸前；0.6-1.3s B 从泥地上抬起视线看向 A；"
            "1.3-2.3s 保持稳定构图，两人几乎静止，只有呼吸与发丝微动。\n"
            "尾帧必须清晰稳定、不淡出、不切黑，可整帧导出。",
    ),
]


def build(name="forest_fight_20s_p2", tier="quality", mp=0.5):
    shots = []
    for i, s in enumerate(SHOTS):
        shots.append({
            "i": i + 1,
            "start": round(i * SEG, 2),
            "end": round((i + 1) * SEG, 2),
            "beat": s["beat"],
            "camera": s["camera"],
            "sound": s["sound"],
            "prompt": prompt(s["act"], s["camera"], s["sound"], first=(i == 0)),
            "refs": [],
            "start_frame": "",          # filled by h3_chain_next.py
            "check": {},
        })
    return {
        "name": name,
        "start_image": "",              # filled by h3_chain_next.py
        "tier": tier,
        "megapixels": mp,
        "segment_frames": FRAMES_PER_SEG,
        "segments": len(shots),
        "crossfade": 0.15,
        "writing_profile": "longform",
        "total_seconds": 20,
        "shots": shots,
        "prompts": [s["prompt"] for s in shots],
    }


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\codex-text\h3_runs\forest_fight_20s_p2\spec.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    spec = build()
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lens = [len(p) for p in spec["prompts"]]
    exc = len(spec["prompts"][0].split("排除：")[-1])
    print("wrote %s" % out)
    print("  segments      : %d" % len(spec["shots"]))
    print("  提示词长度    : %d-%d 字" % (min(lens), max(lens)))
    print("  排除段长度    : %d 字（round 1 是 241 字）" % exc)
    print("  排除段含身体词: %s" % any(w in spec["prompts"][0].split("排除：")[-1]
                                      for w in ("手", "指", "肢体", "臂", "肘", "关节")))


if __name__ == "__main__":
    main()
