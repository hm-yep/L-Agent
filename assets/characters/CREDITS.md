# 素材来源与授权（必读）

本项目 `assets/characters/` 下的角色动画素材来自第三方，使用前请遵守对应授权。

---

## Chibi Knight（角色精灵图）

| 项目 | 内容 |
|------|------|
| 名称 | 2D Knight Chibi |
| 作者 | Segel T |
| 来源 | https://opengameart.org/content/2d-knight-chibi |
| 授权 | **CC-BY 3.0** / OGA-BY 3.0 |
| 下载 | https://opengameart.org/sites/default/files/CHIBI%20KNIGHT-PNG.zip |

**CC-BY 3.0 要求署名**。若你分发本项目（含打包后的 exe），需要在
「关于 / 致谢」或等效位置保留以下署名文字：

> Chibi Knight sprite by Segel T (https://opengameart.org/content/2d-knight-chibi),
> licensed under CC-BY 3.0.

### 本项目对素材做的修改

原作者素材为 **朝左**，本项目按框架约定（素材朝右为正方向）做了如下处理：

1. **水平翻转** → 全部帧改为朝右；
2. **统一裁剪** → 取 Idle / Run / Attack / Hurt / Jump 五组动画的联合包围盒
   `(57, 40, 484, 487)`（09-11 接入 Attack 后重算，此前为 `(65, 43, 477, 487)`），
   裁掉四周空白，保证各动画帧间不跳动；
3. **归一化** → 等比缩放到 512×512 主画布，脚底对齐到画布 90.6% 高度线
   （与旧版程序化橘猫的脚底位置一致，故粒子特效锚点无需改动）；
4. **补绘** → `think` / `searching` / `talk` 三组动画原素材没有，以 Idle 帧为底，
   叠加手绘的思考泡泡问号、左右张望箭头、飘出音符（描边色取自骑士素材的
   深灰蓝 `#484A58`，配色与素材一致）；
5. **补帧** → 素材的 Jump 动画只有 2 张真实帧（Jump_Up / Fall_Down），
   以这两张帧为底绕脚底线做挤压/拉伸（squash & stretch）合成 8 帧
   完整跳跃弧（下蹲蓄力 → 起跳拉伸 → 腾空 → 下落 → 落地压缩 → 恢复），
   脚底与水平中心锚定不动；`think` 的歪头角度亦逐帧变化增强动感。
6. **接入 Attack** → 素材原有的挥剑动画（8 帧）加工后作为 `attack` 姿态
   （09-11），用于打开应用成功的庆祝动作，未增删任何帧内容。

原始未修改素材保留在素材包的 `CHIBI KNIGHT-PNG/` 目录中，未随项目分发。

---

## 音效（assets/sounds/）

来自 **400 Sounds Pack**，挑选了 UI / Card and Board / Other 分类下的若干音效。
具体授权以该素材包原始说明为准。

---

## 内置程序化绘制（兜底）

若 `assets/characters/<姿态>/` 为空，动画会回退到 `ui/sprite_manager.py`
中内置的程序化绘制角色（橘猫团子），该部分是项目原创代码，无第三方授权问题。
