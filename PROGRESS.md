# 角色素材替换 · 进度存档

> 状态：**✅ 09-14 骑士形象统一完成**——exe 图标换成骑士徽章、说话口吻
> 全面改成骑士风（AI 提示词 + 写死台词 + 离线兜底）、截图前回复停留缩到 2.5 秒、
> 新增一键打发布包脚本。
> 当前版本 = 标签 `rollback-safe-5`。
> **回退点**：`rollback-safe`（87b75e2，情绪表演完成、挥剑接入前）/
> `rollback-safe-2`（5ab38e2，09-11 七姿态全功能）/
> `rollback-safe-3`（b34cc5d，09-12 AI 稳定+桌面快捷方式打开）/
> `rollback-safe-4`（5f0ab95..ef18be0，09-13~14 历史面板升级 + 打包共用 data +
> 右键菜单精简 + 截图动作，详见「七」）/
> `rollback-safe-5`（09-14 骑士图标 + 骑士口吻 + 截图 2.5 秒，详见「八」）。
> 存档时间：2026-09-10（09-11、09-12、09-14 更新：见「验证状态」「七」「八」）

---

## 一、这次要做什么

把桌宠的角色从**代码程序化绘制的橘猫团子**换成**真实的 2D Q版人物序列帧素材**，
要求包含跑步（对应移动）、跳跃等互动动作。

原橘猫由 `ui/sprite_manager.py` 里的 `QPainter` 绘制逻辑实时生成，不依赖任何素材文件。

---

## 二、关键决策与理由

| 决策 | 结论 | 理由 |
|------|------|------|
| 素材选用 | **OpenGameArt「2D Knight Chibi」** | 最初选的 CraftPix Chibi Boy **必须先登录才能下载**（邮箱或 Google/Facebook），无法自动获取；itch.io 镜像在本机被网络层阻断，连不上 |
| 备选否决 | Hero Guy（同作者） | 已下载对比，角色拿砍刀/步枪、表情凶狠、杀气重，不适合当桌宠 |
| 缺失动作 | **以 idle 帧为底补画** | 素材包只有 待机/跑/跳，缺 think/searching/talk。用真实帧作底 + 叠加状态符号，保证角色本身 100% 一致 |
| 播放方案 | **PNG 序列帧**，非 GIF | GIF 只有 1-bit 透明通道，边缘会锯齿。PNG RGBA 是 8-bit 透明，且能精确控制缩放 |
| 兜底 | 保留程序化橘猫 | 加载优先级为 序列帧 > GIF > 程序化绘制，`assets/characters/` 为空时自动回退，改动是增量安全的 |

### 一个容易误判的点

CraftPix 那个素材页写着 **"This asset is made in AI"**，但它的文件清单是
`1 AI files; 62 EPS files; 62 body parts`，且是 **2017 年**的素材（DALL·E 1 要 2021 年才出现）。
这里的 **AI 指 Adobe Illustrator，不是人工智能**。此结论由证据推断，未 100% 证实。

对最终选用的骑士素材，做了色彩分析佐证非 AI 生成：**全图仅 913 个精确 RGB 值**、
灰阶平涂为主（AI 生成图通常有上万噪点色值），且作者署名 Segel T 可查。

---

## 三、已完成

### 1. 帧预处理（可复现）

原始素材：42 张 **550×598 RGBA**，6 组动画（Idle/Run/Attack/Jump/Hurt/Die），
各帧对齐一致（脚底均在 y≈485），**原素材朝左**。

处理流程（已固化为脚本 `tools/prepare_character_frames.py`）：
1. **水平翻转** → 改为朝右（框架约定朝右为正方向）
2. **统一裁剪** → 取 Idle/Run/Attack/Hurt/Jump 五组的联合包围盒 `(65,43,477,487)`
   （逐帧各自裁剪会导致播放时抖动）
3. **归一化** → 等比缩放到 512×512 画布，脚底对齐 **90.625% 高度线**、水平居中

> **脚底 90.625% 这个比例不是随便定的**：它等于旧橘猫的 116/128，
> `_spawn_particles` 里硬编码的粒子坐标（尘土 y=112 等）依赖它。
> 改这个值必须同步改粒子坐标。

### 2. 六姿态齐备

| 姿态 | 帧数 | 唯一帧 | 来源 |
|------|------|--------|------|
| idle | 8 | 5 | 原素材 Idle |
| walk | 8 | 8 | 原素材 Run |
| jump | 8 | 8 | 原素材仅 Jump_Up/Fall_Down 两帧，绕脚底线 squash & stretch 合成完整跳跃弧（09-11 补帧） |
| attack | 8 | 6 | 原素材 Attack（挥剑），09-11 接入，弓步前冲属真实动作 |
| think | 6 | 6 | 补画：歪头（角度逐帧变化）+ 思考泡泡问号 |
| searching | 6 | 6 | 补画：左右张望箭头 + 身体轻移 |
| talk | 6 | 6 | 补画：上下弹跳 + 飘出音符 |

补画/补帧脚本已固化为 `tools/make_derived_poses.py`，符号用 `seguisym.ttf`
（普通 Segoe UI 没有 ♪ 字形，会渲染成方块）。
jump 补成 8 帧的依据：jump 姿态 14fps，PetWindow 一跳 280+280=560ms ≈ 8 帧，刚好播完一轮。

### 3. 加载器接入 `ui/sprite_manager.py`

- 新增 `_sequence_paths()` / `_seq` / `_use_seq`
- `set_pose()` 优先级改为：**PNG 序列帧 > GIF > 程序化绘制**
- `advance()` / `current_pixmap()` 增加序列帧分支，帧数动态取值（不再写死在 `POSES`）
- 尺寸变化时按 `Qt.SmoothTransformation` 重缩放并缓存

### 4. 工具与文档

| 文件 | 作用 |
|------|------|
| `tools/prepare_character_frames.py` | 素材包 → 序列帧，带 knight/heroguy 预设，支持 `--pose` 自定义映射、`--dry-run` |
| `tools/make_derived_poses.py` | 以 idle 为底补画 think/searching/talk |
| `tools/verify_character_frames.py` | 校验帧尺寸/脚底漂移/动画有效性，`--render` 附加离屏渲染总览图 |
| `assets/characters/CREDITS.md` | **CC-BY 署名要求**，含本项目对素材做的 4 项修改说明 |
| `docs/preview/*.png` | 4 张预览图（暂存目录可能被清理，故复制进项目） |

---

## 四、验证状态

### 已做：数值校验（**通过**）

走真实代码路径离屏渲染（`set_pose → advance → current_pixmap → draw_overlay`，128px 含粒子层），
逐帧测量脚底基准、中心、唯一帧数、不透明像素数。

**核心结论：脚底稳定在 y=116（128px 缩放后），与旧橘猫完全一致 → 粒子特效锚点无需改动。**

### 未做

（无 —— 人工目视验收已于 09-11 由用户确认通过）

### 打包验证：已通过（09-11，补帧后重新打包）

`python -m PyInstaller build/desktop_pet.spec`（PyInstaller 6.22.2，Python 3.14）
构建成功，产出单文件 `dist/桌面AI伙伴.exe`（约 47MB）。
用 `CArchiveReader` 检查 exe 内部归档：**六姿态 42 帧全部进包且为补帧后最新版本**
（idle 8 / walk 8 / jump 8 / think 6 / searching 6 / talk 6）、
`CREDITS.md` 在包内、音效 9 个在包内。未实际启动 exe 验证运行。

### 实机启动：已于 09-11 实际跑过一次（非 --selftest，真实交互）

`data/logs/app.log` 中 14:54:57 的启动轮次：

- 整轮**没有出现** `[Sprite] 生成 … 帧 @128px` 日志（该日志只在程序化绘制
  `_build_frames` 里打，此前每次启动都有）→ 走的是**序列帧分支**，未回退橘猫
- 0 条 WARN/ERROR；完整交互链跑通：气泡 → move_to → open_app 微信 → jump → 连续 wander

注意：这只能证明 idle 用了序列帧且整体不崩；walk/jump 姿态切换的实际观感
仍需目视验收。`python main.py --selftest` 自动化冒烟测试本身仍未跑过。

### 校验脚本误报：已修复（09-11）

原全局容差 `FEET_TOLERANCE=4 / CENTER_TOLERANCE=8` 会把三个姿态的
**有意的表现性动作**误报为缺陷。已改为按姿态可配：`POSE_TOLERANCE`
（walk/searching 中心容差放宽到 14，talk 脚底容差放宽到 8），其余姿态仍用全局值。

| 姿态 | 原报警 | 实际原因 |
|------|--------|----------|
| walk | 中心漂移 248~261 | **原素材 Run 本身**的身体前后倾，正常 |
| searching | 中心漂移 243~255 | **补画时有意的** ±6px 张望摆动 |
| talk | 脚底漂移 463~469 | **补画时有意的** ±7px 说话弹跳 |

修复后 `verify_character_frames.py`（含 `--render`）输出 **ALL PASS**。
各姿态静止基准 y=463 与规范 464 差 1px，系 `int()` 取整，无视觉影响。

### jump/think 补帧：已完成（09-11）

- **jump 4→8 帧**：原素材只有 Jump_Up/Fall_Down 两张真实帧，新加
  `squash_stretch()` 绕脚底线做纵向挤压/拉伸（水平按 `sy^-0.6` 半体积守恒），
  按跳跃弧合成 8 帧（蓄力 0.94 → 起跳 1.06 → 上升 → 顶端 → 下落 1.05 →
  落地 0.93 → 恢复）。实测：8 帧全唯一，**脚底 463~463 零漂移、中心零漂移**，
  逐帧身高比例与设计弧线吻合。帧内只形变不位移——跳跃位移由 PetWindow.move_to 完成。
- **think 3→6 唯一帧**：歪头角度由固定 -5° 改为 `THINK_TILT` 表逐帧摆动
  （-4°~-7°），与泡泡浮动叠加后 6 帧全不同。实测中心漂移 262~269（7px，
  在默认容差内）、脚底 462~463。
- 校验 `ALL PASS`；总览图已重渲染到 `data/character_render_sheet.png`。

---

### attack 挥剑姿态接入（09-11 晚，git 存档点 rollback-safe = 87b75e2 之后）

- 素材包自带 Attack 组 8 帧，加入 knight 预设后随联合包围盒统一归一化安装；
  打开应用成功的庆祝由「跳跃」改为「挥剑」（attack + happy + 音符金星），
  UI 层播完一轮（约 664ms）自动回 idle；AI 动作池的 `jump` 保持不变
- **联合包围盒重算**：接入 Attack 后为 `(57, 40, 484, 487)`（原 `(65, 43, 477, 487)`，
  此前存档「Attack 已含在旧包围盒」的说法有误），七姿态已全部按新盒子统一
  重归一化，脚底精确落在 y=464；CREDITS.md 已同步
- 校验：attack 中心漂移 220~264 为挥剑弓步真实动作，`POSE_TOLERANCE` 放宽到 48，
  七姿态 ALL PASS
- 防呆：`make_derived_poses.py` 检测到 jump 目录帧数 >4（已是补帧产物）时拒绝
  再次补帧，防止挤压拉伸二次叠加

## 五、下一步

（无 —— 任务收尾。日常换素材时按「四」末尾的工具链三步走即可；另注意 `.venv`
已损坏指向不存在的 `D:\class\Python\python.exe`，当前一律用系统 Python 3.14。）

### 若想换成别的素材包

加载器是**通用**的。拿到素材包后：

```bash
python tools/prepare_character_frames.py <角色名> --src "<素材包目录>" \
    --pose idle=01-Idle --pose walk=02-Run --pose jump=04-Jump
python tools/make_derived_poses.py
python tools/verify_character_frames.py --render
```

前置条件：素材**朝右**（朝左要加 `--flip`）、透明背景 PNG 序列、各帧画布一致。
CraftPix 那套若哪天手动下到，直接按上面三条跑即可替换。

---

## 六、暂存目录

`C:\Users\WHZ\Downloads\desktop_pet\_assets\`（**独立于项目，删除不影响项目**）

内含原始素材包、解压产物、预览图、临时校验脚本。
需要保留的预览图已复制到 `docs/preview/`，但**原始素材包仅存于此**，
若要重新预处理请先备份该目录。

---

## 七、存档点 `rollback-safe-4`（09-13 ~ 09-14）

覆盖 `rollback-safe-3`（8346cdc）之后的 **4 个提交**。
**以下内容摘自提交信息，未逐行核对代码。**

| 提交 | 日期 | 内容 |
|------|------|------|
| `5f0ab95` | 09-13 | 历史记录面板升级：记录按钮改「历史记录」并加高，每条记录新增复制/删除（按轮删除） |
| `6724777` | 09-13 | 打包版与开发版共用项目 `data` 目录（exe 在 `dist` 下时），避免聊天记录分裂 |
| `b46cfb2` | 09-14 | 右键菜单精简为三项；新增**截图动作**：隐藏入镜 → 抓全屏 → 另存为对话框保存 |
| `ef18be0` | 09-14 | 截图前先展示确认回复：停留时长与气泡定时同规则（封顶 8 秒），看清后再隐藏抓屏 |

### 回退这个存档点前必须知道的三件事

1. **构建产物不在版本控制内**。`.gitignore` 排除了 `data/`、`build/`、`dist/`，
   所以 `dist/桌面AI伙伴.exe` 不受回退影响 —— 回退代码**不会**带回旧 exe，
   需要重新执行 `python -m PyInstaller build/desktop_pet.spec` 打包。
2. **`data/` 同样被忽略**。它承载 `memory.json`（聊天记录）、`user_settings.json`
   （含明文 API Key）、`logs/`。回退代码不会动这些，聊天记录与设置会保留。
3. **标签是附注标签**（有 tag 对象），与 `rollback-safe` ~ `-3` 一致。
   回退用 `git checkout rollback-safe-4`（或 `git reset --hard rollback-safe-4`，后者会丢改动）。

### 本次存档时的环境事实

- Python：系统 **3.14**（`.venv` 已损坏，指向不存在的 `D:\class\Python\python.exe`，勿用）
- 工作区状态：**干净**，无未提交改动，无未跟踪文件
- `rollback-safe-3` 的标签实际指向 `8346cdc`，而「四」末尾出处记为 `b34cc5d`
  —— 该处引用的是功能提交，二者相差一个文档提交，不影响回退

---

## 八、存档点 `rollback-safe-5`（09-14 下午）

主题：**骑士形象统一（图标 + 口吻）+ 截图时序调整 + 发布包脚本**。
本次为一次性功能存档（未中途提交，4 个修改 + 3 个新文件一并入库）。

### 内容明细

**1. exe 图标换成骑士徽章**
- `tools/make_icon.py`（新增）：从 `assets/characters/attack/attack_003.png`
  （挥剑帧）生成圆形徽章图标——橙色环（同 UI 主题 #e07b39）+ 奶油渐变底 +
  整身骑士。脚本自动求解角色不超出圆内切区的最大缩放，并按角色实际落点画投影。
- 设计依据：骑士素材为银灰铠甲+深描边（三档灰占 74% 像素），深底会吞描边，
  故选浅底；整身入画避免半身裁剪切坏头盔/剑。
- `assets/icon.ico`（7 档尺寸不变）与 `assets/icon.png`（512 主图）已重制；
  `ui/ui_manager.py` 托盘图标的程序化兜底由橘猫改为骑士徽章。
- 验证：`_assets` 期校验 7 档齐全、圆外溢出 ≤1px（抗锯齿）、16px 下银甲占比 47%；
  离屏 Qt 加载通过；从打包 exe 抽出的内嵌图标与新 ico 逐像素一致。
- 预览图：`docs/preview/05_骑士图标预览.png`（新旧对比，浅/深底）。

**2. 说话口吻骑士化（角色是骑士，不再软萌）**
- `framework/ai_engine.py` SYSTEM_PROMPT：写入正式骑士人设（身披银甲、佩剑），
  硬规则禁用软萌语气词（呀/啦/哦/哟/嘛/嗯嗯）与波浪号；三种性格 chat_style
  （`framework/config.py`）改写为骑士风（豪爽年轻骑士/老骑士/毒舌副官）。
- 写死台词约 20 句全部换口吻（core.py 的截图/打开/性格切换/忙线/主动搭话、
  ai_engine.py 离线兜底、tools/mock_ai_server.py）。
- 全库扫描确认无残留软萌语气词。

**3. 截图前确认回复停留 2.5 秒**
- 原按气泡规则（3.5s + 180ms/字，封顶 8s），改为固定
  `config.SCREENSHOT_REPLY_MS = 2500`（`framework/core.py` 引用）。
- 已核对 `pet_window._on_screenshot_hide` 会连气泡一起隐藏，缩短等待不会让气泡入镜。

**4. 发布包脚本**
- `tools/make_release_zips.py`（新增）：一键生成 exe 包（桌面AI伙伴.zip）与
  源码包（desktop_pet.zip）。源码包排除 `.venv/build/dist/.git/data` 等，
  **data/ 含明文 API Key，必须排除**；内置敏感文件（data/、user_settings.json、
  memory.json）校验。注意脚本会跳过 `.zip`，防止打包残留被收进包里。

### 回退注意事项（与前几个存档点相同）

1. `dist/` 不在版本控制内：回退代码不会带回旧 exe，需重新
   `python -m PyInstaller build/desktop_pet.spec`（另：桌面AI伙伴.zip 也要重打）。
2. `data/` 被忽略：聊天记录与设置不受回退影响。
3. 标签为附注标签，回退用 `git checkout rollback-safe-5`。

### 本次存档时的环境事实

- Python：系统 **3.14**（`.venv` 仍损坏，勿用）
- PyInstaller 6.22.2；exe 已用本存档代码重新打包并实测运行
- 桌宠当时正在运行（169156 等进程），属正常现象，与回退无关
- 发布包 `desktop_pet.zip` / `桌面AI伙伴.zip` 在项目上级目录
  （`C:\Users\WHZ\Downloads\desktop_pet\`），不在版本控制内

---

## 九、09-15 追加修复：聊天记录长回复显示不全

### 问题

用户让皮皮写代码等长回复时，历史记录面板里的回复显示不全。

### 根因（`ui/input_dialog.py`）

存储层完整（记忆系统存全文不截断），问题在显示层两处：
1. 单条记录高度被 `ITEM_MAX_HEIGHT = 320` 硬性封顶，超长内容被 QLabel 直接剪掉
   （注释声称"靠滚动"，但 QLabel 并不滚动）；
2. 高度按"每字符都是全角中文"粗估，对代码类半角文本偏差大。

### 修复

1. 面板扩大：`PANEL_WIDTH 400→560`、`PANEL_HEIGHT 520→640`；
2. 去掉 320px 高度封顶，条目高度改用 `QLabel.heightForWidth()` 按真实排版
   精确计算（含 QSS 内边距与条目留白余量），长内容完整展开、靠列表滚动查看；
3. 会话标题宽度同步改为常量口径（面板未显示时宽度读取不准）。

### 验证

- 离屏测试：120 行代码回复条目高度 4057px 完整展开（旧逻辑会裁到 320px），
  短消息保持 77px 紧凑，均不裁字；
- `python main.py --selftest` 11/11 PASS。

### 附：09-15 桌面图标整理功能调研结论（未合入）

曾实现"按类型整理桌面图标"（organize_desktop 动作，方案A：精确摆位+快照还原），
真机验证发现 **Windows 11 25H2（build 26200）桌面图标新实现使精确摆位失效**：
SysListView32 兼容外壳读取正常，但 LVM_SETITEMPOSITION 被拒绝、
LVM_SETITEMPOSITION32 返回成功却无视坐标（系统自动安放），重启 Explorer 与
调整 DPI 感知均无效（Fences/DesktopOK 类工具同因失效）。经用户确认整段回滚，
未提交。若未来系统恢复接口或改用模拟拖拽方案，可参考本记录重新实现。

---

## 十、09-15 追加功能：语音调音量（系统音量 / 单应用音量）

### 功能

用户说"帮我调节音量，调到50"→ 皮皮把**系统总音量**调到 50；
说"把微信的音量调到30"→ **只调微信的声音**（Windows 音量合成器会话），不动系统音量。

### 实现

1. `framework/volume_controller.py`（新增）：纯 ctypes COM，无第三方依赖。
   - 系统音量：IMMDeviceEnumerator → 默认输出设备 → IAudioEndpointVolume
     （SetMasterVolumeLevelScalar + 顺带解除静音）；
   - 应用音量：IAudioSessionManager2 → 枚举声音会话 → IAudioSessionControl2
     取进程 ID → 按进程名匹配 → ISimpleAudioVolume.SetMasterVolume。
2. 关键坑（Python 3.14 + Win11 26200 实测）：
   - 3.14 移除了 ctypes.GUID / wintypes.GUID，需自定义 GUID 结构体；
   - 3.14 的 HRESULT 返回失败时会抛 OSError（不再能靠 _ok(hr) 判断）；
   - IMMDevice::Activate 虚表槽位是 **3**（不是文档阅读习惯的 5）；
   - IAudioEndpointVolume：SetMasterVolumeLevelScalar=7 / Get=9 / SetMute=14；
   - IAudioSessionManager2 的 IID 是 {77AA99A0-...-4C9A**9B**6F}（此前误写
     3B6F 导致 E_NOINTERFACE，曾误判为系统限制）；
   - IsSystemSoundsSession 返回 S_FALSE 表示普通会话，不能用 >=0 判断。
3. 应用名匹配：中文应用名经 `config.APP_WHITELIST` 映射到 exe 名
   （如 微信→wechat.exe），再加进程名包含匹配；会话仅对"正在运行且出过声"
   的应用存在，没找到时明确提示且不动系统音量。
4. 动作层：`set_volume`（必填 volume 0~100，可选 app），提示词第 10 条 +
   规则；离线模拟与意图兜底共用数字/应用名提取（支持 50 / 30% / 百分之三十 /
   调到十五），没提数字不兜底；越界钳制 0~100，坏参数气泡提示不崩溃。

### 验证

- 真机：系统音量 40 ↔ 原值往返、250 钳到 100、坏参数不崩、
  应用会话按关键词部分匹配（wall→wallpaper32.exe）全部通过；
- `python main.py --selftest` 11/11 PASS。

---

## 十一、09-15 合入电源功能 + 修复设置窗口显示不全

### 1. 合入队友交付的电源功能（来源：`desktop_pet1.zip`）

`framework/power_controller.py`（新增）+ `config/action_parser/ai_engine/core.py`
四处订正，动作池扩到十四个：`shutdown` / `restart` / `lock` / `cancel_shutdown`。

- 关机/重启走 `shutdown /s|/r /t N`，默认延时 `POWER_ACTION_DELAY = 60` 秒，
  期间可用「取消关机」撤销（`shutdown /a`）；锁屏立即执行（`rundll32` LockWorkStation）。
- 总开关 `ALLOW_POWER_ACTIONS`：关掉后一律拒绝并回气泡提示。
- 合并时补回被去掉的文件末尾换行，其余原样保留。

### 2. 设置窗口显示不全（用户报障）

**现象**：右键「设置」，设置页下半截和右侧看不到，保存/退出按钮点不到。

**根因**（两个缺陷叠加，前者是主因）：

1. **弹窗位置没有夹紧到屏幕**（`ui/pet_window.py` 的 `show_settings`）：
   桌宠默认待在屏幕右下角（锚点 `bottom_right`），设置窗口按固定偏移
   `(pet.x-80, pet.y-260)` 摆放。本机 2560x1600 实测：设置窗口落在
   `(2234, 1094, 360, 620)`，右下角到 (2593, 1713) —— **右侧溢出 34px、
   底部溢出 114px**，也就是 620px 高的窗口有 18% 在屏幕外，最底下的
   保存/退出整行看不见。
2. **面板没铺满窗口**（`ui/ui_manager.py` 的 `SettingsDialog`）：
   `panel = QWidget(self)` 是个游离子控件，既没进布局也没设几何，靠 Qt 的
   "布局激活时按 sizeHint 自撑"落到 301x605，漂在写死 360x620 的窗口里
   （96dpi 下更糟：403x555 比窗口还宽 21px，右侧直接被切）。

**修复**（`ui/ui_manager.py` + `ui/pet_window.py` + `ui/style.qss`）：

1. 设置窗口改为**按内容定尺寸**：宽度取内容 sizeHint 并夹在 360~520 之间，
   高度不超过屏幕可用高度的 88%；内容再高也能滚动（装进 `QScrollArea`，
   只在该出现时才显示滚动条）。
2. 新增 `show_near(anchor)`：先量尺寸再**夹紧到屏幕可用区域**（留 8px 边距），
   上方放不下就翻到角色下方；`_clamp_into_screen()` 同时用于拖动过程，
   窗口拖到屏幕外会贴边停住，不会拖丢。
3. 面板铺满窗口后原来那圈透明边没了，原来"只剩边角能拖"，
   改为用 `eventFilter` 让面板空白处也能拖整窗（输入框/下拉框上的事件
   由子控件自己吃掉，不影响点击）。
4. `set_ai_status` 变长时重算尺寸，状态文案再长也不会把底部按钮顶掉。
5. `_available_screen()` 抽成可注入方法，便于测试矮屏幕场景。

### 验证

- 设置窗口专项 20 项检查全部 PASS（真实 144dpi / 2560x1600）：
  面板铺满窗口且无偏移、无控件越界或被压窄、五个极端锚点（含屏幕外）
  弹出后整窗都在屏幕内、圆角外 alpha=0（透明正确）、矮屏幕 1280x600 下
  高度夹到 528 且滚到底「保存」可见、面板空白处可拖动、拖出屏幕贴边停住、
  长状态文案下窗口自动长高。
- 电源功能 30 项测试 PASS：解析白名单（delay 支持"延时"/"90秒"）、
  离线模拟六种口语说法、真实命令拼装（`shutdown /s /t 30`、`/r /t 60`、
  `/a`、LockWorkStation）、Framework 执行分支（关机/重启/锁屏/取消）、
  总开关拦截、延时夹紧（默认 60 / 负值归零 / 99999 封顶 3600）。
  **测试对 `subprocess.Popen` 打桩，绝不真实关机。**
- 音量功能回归 15 项 PASS（系统音量 40 往返后恢复原值 56%）。
- `python main.py --selftest` 11/11 PASS。

### 追加：把设置窗口检查写进自测（回归防护）

`main.py` 的 `run_selftest` 增加 T+8s 的**设置窗口检查**（4 项）：窗口弹出、
面板铺满窗口、整窗在屏幕内、内容没有被压窄。顺手把 `check()` 扩成可带 detail
（原来只收 2 个参数，多传一个会在定时器回调里抛 TypeError 并让进程直接 abort）。

自测从 11 项增至 **15 项**，可直接在打包产物上跑，等于让 exe 自己验证设置窗口：

```
dist\桌面AI伙伴.exe --selftest
```

本机实测结果（无控制台，结果写进 `data/logs/app.log`）：

```
[SelfTest] 设置窗口已弹出: PASS
[SelfTest] 设置面板铺满窗口: PASS
[SelfTest] 设置窗口整窗在屏幕内: PASS (窗口 (2107, 888, 445, 619) / 屏幕 (0, 0, 2560, 1600))
[SelfTest] 设置内容没有被压窄: PASS
[SelfTest] 完成: 15/15 通过
```

另新增 `tools/verify_exe_package.py`：直接从 exe 的归档里核对
①修复代码确实打进去了（按符号查 PYZ 模块）②**API Key / user_settings.json /
memory.json 没有被打进包**③assets/icon.ico 与 ui/style.qss 资源齐全。
本机结果：11 项全 PASS（密钥长度 35，未出现在 1.17 亿字节归档中）。

### 关于窗口宽度

设置窗口宽度按内容定，实测本机是 **445x619**（不是原来写死的 360x620）：
AI 状态文案（"✓ 已连接 DeepSeek…"）较长，需要更宽才能单行放下。
宽度上限 520，超过则状态文案自动换行；高度上限是屏幕可用高度的 88%，
再高就滚动——两种情况都不会有设置项被切掉。

---

## 十二、09-15 追加修复：设置页面变成透明的

### 根因

修"显示不全"时给设置窗口滚动区加的透明规则写宽了：

```css
QScrollArea#settingsScroll > QWidget > QWidget { background: transparent; }
```

这条本意是清掉滚动区**视口**的底色，但设置面板正好是"视口的子控件"
（面板 → qt_scrollarea_viewport → QScrollArea），被这条规则连带命中；
而且它带两个类型选择器，优先级比 `#settingsPanel { background-color:#fffaf2 }`
高，把面板的奶油底色清成了透明——整页就"透"出桌面了。

之前自测没抓到是因为只采样了"窗口中心"（那里恰好压在不透明的输入框上）
和四角（本来就应该是透明的），漏了"面板纯底色"的像素。

### 修复与防护

- 改为只清视口：`QScrollArea#settingsScroll > QWidget#qt_scrollarea_viewport`
  （按视口对象名精确命中，不再波及面板），并在 QSS 里注明原因；
- `tools/test_settings_dialog.py` 补一项**面板底色采样**检查（取内容边距之外的
  纯底像素，要求正好是 #fffaf2 不透明），专项检查增至 21 项，全 PASS；
- 打包后再从 exe 里抽出 `ui/style.qss` 核对：问题规则不在包内、
  视口规则与面板底色规则都在。exe 自测 15/15 通过。

---

## 十三、09-16 用户设置改存 %APPDATA%：exe 挪位置不丢 API Key

### 现象

用户反馈：每次打开打包好的 exe 都要重新输入 API Key。

### 根因

设置本来就持久化到 `user_settings.json`，但路径是"exe 往上两级找到 `data/`，
否则用 exe 旁边的 `data/`"。这只在 exe 待在 `dist/` 时成立；一旦 exe 被
复制/移动到别的文件夹，或直接在压缩包里双击运行（Windows 会解到临时目录），
设置就会写到"当前位置旁边的 data"，位置一变等于全部丢失。

### 修复

- `framework/config.py`：用户设置固定存到 `%APPDATA%\DesktopPet\user_settings.json`
  （每台电脑唯一稳定的位置），首次运行时自动从老位置 `data/user_settings.json`
  迁移一次；并修复"全新用户首次保存时目录不存在"的问题（`os.makedirs`）；
- 记忆文件/日志仍走 `app_data_dir()`（exe 在 dist 时与开发版共用，方便调试）；
- `main.py --selftest` 增至 **16 项**：新增"用户设置可读写(存APPDATA)"；
- 新增 `tools/test_settings_persistence.py`（10 项）：读写回环、APPDATA 路径、
  老位置迁移、全新用户首存、有 Key 自动切真实 AI；
- `tools/verify_exe_package.py` 增加两项：包内 `framework.config` 确用
  APPDATA 固定存储；密钥来源优先读新位置再读老位置。

### 验证

- 专项测试：持久化 10/10、设置窗口 21 项、电源 30 项、音量 15 项，全 PASS；
- `python main.py --selftest` → 16/16；`dist\桌面AI伙伴.exe --selftest` → 16/16；
- exe 包校验全 PASS（含"API Key 未被打包"）；
- 重启 exe：日志 `已载入用户设置: model=deepseek-v4-flash`、`USE_REAL_AI=True`
  —— 设置从 %APPDATA% 正常加载，无需重填 Key；
- 源码包 106 项（+1 个新测试文件），敏感文件检查通过。

---

## 十四、09-16 AI 调用本机工具生成文档（PPT / Word / Excel）

### 需求

让皮皮通过内嵌大模型调用电脑上的工具，实现制作 PPT、生成文本表格等文档能力。

### 方案

沿用现有「AI 返回 JSON 动作数组 → action_parser 校验 → core 分发执行」链路，
新增第 15 个动作 `create_doc`：大模型负责把需求转成结构化内容（PPT 每页标题+要点 /
Word 章节段落 / Excel 表头+行数据），新模块 `framework/doc_generator.py` 用
python-pptx / python-docx / openpyxl 在本地渲染成文件。保存位置由用户在弹出的
"另存为"对话框选择（与截图保存同款交互），生成在 QThread 后台执行，完成后
气泡 + 卡片反馈路径和大小。

### 实现要点

- `doc_generator.py`：三种文档渲染 + 规模防御（页≤30、表行≤500、单段≤2000 字，
  防模型输出失控），任何异常以 (False, 原因) 返回不让桌宠崩溃；
- `ai_engine.py`：SYSTEM_PROMPT 动作池加 create_doc 及三种 content 结构约定
  （PPT 限制"每页 3~5 条短要点、最多 10 页"防大段文字塞爆版式）；mock_ai 加
  离线演示内容；文档意图命中的请求 max_tokens 下限提到 4096（原 1024 装不下
  整份 PPT），截断放大上限同步放宽到 16384；
- `core.py`：`_DocWorker(QThread)` 后台渲染；`_do_create_doc` 做总开关检查、
  filename 只取 basename（防模型给全路径）、扩展名补全；意图兜底放最后一位，
  避免劫持"打开 word 文档"类指令；
- 设置界面新增「允许皮皮创建文件」复选框，持久化到 %APPDATA%（doc_enabled）。

### 验证

- 新增 `tools/test_doc_generator.py` 40 项断言全过：解析/别名/类型归一、
  离线模拟、三种文档真实生成+用对应库回读、规模防御裁剪、总开关拦截、
  对话框打桩后的完整执行链（QFileDialog 打桩→后台线程→卡片反馈→落盘）；
- `python main.py --selftest` 与 exe 自检均 **17/17**（第 17 项在打包环境
  真实生成临时 PPT 验证落盘）；
- `verify_exe_package.py` 新增检查全过：doc_generator/core/parser 代码入包、
  pptx/docx/openpyxl 三库入包、pptx 默认模板数据入包、API Key 未泄露；
- exe 体积 70.8 MB；发布包 108 项，敏感文件检查通过。

---

## 十五、09-18 PPT 美化母版 + 联网配图 + 文件修改返回

### 需求

1. 生成的 PPT 全是白底黑字，要美化（方案A：内置美化母版）；
2. 希望能联网找素材配图（方案B：Pexels 免费图库）；
3. 新功能：用户把 pptx/docx 发给皮皮，皮皮修改后返回新文件。

### 实现

- **美化母版（doc_generator 重写）**：PPT 用空白版式 + 文本框全手绘——
  封面深底 + 左侧亮青竖条 + 大标题 + 分隔线；内容页浅底 + 顶部主题色条 +
  标题侧色块 + 要点列表 + 页脚 + 页码；Word 标题/章节上主题色；Excel
  表头主题色填充 + 白字加粗 + 冻结首行。
- **联网配图（方案B）**：SYSTEM_PROMPT 允许 PPT 每页带 `image_query`
  （2~4 个英文搜索词）；设置界面新增「图库Key」（Pexels，选填，存
  %APPDATA%）；`fetch_images()` 在生成线程里搜图下载，PIL 居中裁剪到
  页面比例后插到要点右侧并加主题色描边。没配 Key / 无结果 / 下载失败
  一律安静退回纯排版，最多配 6 张控制等待。
- **文件修改（新增 framework/doc_editor.py + core 状态机）**：
  入口两个——右键菜单「📄 修改文件（PPT/Word）」、把文件直接拖到皮皮身上；
  `extract_content()` 读取文件文字内容（标题占位符 + 文本框，无占位符页
  首行提升为标题，页脚页码过滤），转成与 create_doc 同构的 spec；
  `build_edit_message()` 组装改写任务发 AI，要求返回 create_doc；
  `_apply_doc_edit_result()` 接管动作并预设输出路径「原名_修改.ext」，
  生成不弹保存对话框，完成后卡片反馈。等待期间用户说的下一句话就是
  修改指令，说「不改了/取消修改」退出。模型没给 content 时退回原内容
  重排，不会空手而归。
- 边界：改写是"内容改写"——原文件的图片/动画/母版不迁移，新文件按
  内置母版重新排版；离线模式改文件会提示需要联网 AI。

### 验证

- 新增 `tools/test_doc_edit.py`（31 项，全过）：提取回读（含封面提升、
  页脚过滤、损坏文件归一 ValueError）、改写消息组装、requests 打桩的
  搜图/下载/插图片/无Key不请求、AIWorker 打桩的完整改写链路
  （投喂→指令→预设路径落盘→卡片反馈→状态消费）；
- 老用例回归全过（doc_generator 44 项、设置窗口、电源、音量、持久化）；
- 自测增至 **18/18**（新增「文件内容提取可用」）。
- 按用户要求本轮不重新打包 exe（源码已就绪，下次打包即含全部新功能）。

## 十六、智能体四连：屏幕视觉 / 任务规划 / 长期记忆 / 提醒待办（09-18）

面向"AI+超级智能体"赛道的四项补强，动作池从 15 个扩到 **22 个**：

- **屏幕视觉理解（多模态，see_screen）**：AI 返回
  `{"action":"see_screen","question":...}` → 隐藏桌宠窗口截图 → 压到
  1600 宽存临时 JPG → `_VisionWorker` 后台线程以 OpenAI 兼容图文消息调
  视觉大模型（默认智谱免费 glm-4v-flash）→ 气泡回答。设置界面新增
  「视觉Key」（选填，存 %APPDATA%）；没配 Key 时友好提示申请入口，
  不影响其他功能。
- **任务规划（show_plan）**：复杂任务 AI 先返回 3~6 步计划 → 计划卡片
  亮出；之后每执行一个动作推进一格，队列跑完播报"计划执行完毕/本轮
  推进了 N/M 步"并给 ✔/○ 进度卡片。解析器把字符串步骤自动拆成列表
  （去"第X步/1."前缀）。
- **长期记忆（remember）**：AI 对用户的偏好/信息返回
  `{"action":"remember","fact":...}` → `MemorySystem.facts` 持久化
  （去重、200 字截断、上限 50 条），每轮对话注入系统提示词【长期记忆】；
  右键菜单新增「🧠 我的记忆」可查看/删除/清空。
- **提醒 + 待办（remind / todo_add / todo_done / todo_list，新增
  framework/task_board.py）**：`TaskBoard` JSON 持久化
  （reminders/todos），Framework 每 30 秒轮询到期提醒（重启后错过的
  补播一次）；待办按序号完成、卡片展示清单。时间解析支持"10分钟后/
  半小时/2小时"及中文数字，没说时长默认 30 分钟。
- 提示词与兜底：SYSTEM_PROMPT 加动作 16~22 与对应触发规则；
  `_ensure_tool_intent` 为五类新意图补兜底（模型只回话不给动作时自动
  补动作）；离线 mock_ai 加全部分支（"记住X"不被 PPT 关键词劫持等
  顺序已验证）。

### 验证

- 新增 `tools/test_agent_features.py`（**67 项全过**）：解析器归一化、
  记忆去重/上限/持久化、任务台到期取出/序号完成/回读、mock 新分支、
  AIWorker 提示词注入、视觉消息体构造、Framework 真执行器集成链
  （remember→facts、remind→任务台、show_plan→进度推进→收尾播报、
  无视觉 Key 降级、意图兜底）；
- 老用例回归全过（doc_generator、doc_edit、settings_persistence、power；
  settings_dialog 与 volume 各有 1~5 项失败，经 stash 对比确认为
  HEAD 上的存量环境问题，与本次改动无关）；
- 自测增至 **23 项**，真机 23/23 全过（新增新动作解析、步骤拆解、
  长期记忆、提醒/待办任务台 5 项）。
- 按此前约定本轮不重新打包 exe。

### 十七、计划确认流 + 提醒秒级重做（09-19，用户实测反馈修复）

用户实测发现两类问题：任务规划"出计划就自己开干"、提醒"20秒被记成1分钟/
1分25秒被拆成两条互相冲突的提醒"。本轮全部重做：

- **任务计划确认流**：show_plan 只出计划不再直接执行——弹出常驻计划卡
  （新文件 ui/plan_card.py，窄长条贴桌宠左右侧、随角色移动跟随、截图时
  一起隐藏），卡上有「▶ 开始执行」「✕ 取消」按钮；用户也可直接说话：
  确认词（确认/开始执行/没问题…）= 点确认，取消词（取消计划/放弃计划…）
  = 取消，其他话= 修改意见（自动注入待确认计划上下文让 AI 出修订版）。
- **执行循环**：确认后逐轮执行——每轮让 AI 做当前未完成步骤对应的工具
  动作，做完自动推进下一轮（PLAN_MAX_ROUNDS=8 轮安全阀）；每完成一个
  工具动作卡片上打一个 ✔（说话/表演类动作不打勾，当前步 ▶ 高亮）；
  全部完成后播报+整卡打勾，8 秒自动关。执行中模型没动作时停下交还用户
  （说「继续」接着干）；执行中来新任务会先汇报进度再办新事。
- **提醒秒级化**：extract_remind 重写为复合时长解析——"20秒"=20 秒、
  "1分25秒"=85 秒、"1小时30分"=5400 秒，内容里所有时间词剔除干净；
  remind 动作参数改为 seconds（提示词教模型精确换算，minutes 兼容兼容旧口径）；
  轮询间隔 30s→5s，最短提醒 5 秒（原秒级会被进位成 1 分钟）。
- **提醒防冲突**：一轮回复最多登记一条提醒（重复 remind/show_plan 动作
  丢弃），气泡与任务台都用「N 分 N 秒」格式，不再出现"1.25 分钟"。
- **到点行为**：提醒触发时桌宠连跳 6 次 + 「⏰ 时间到！该去xxx了！」，
  到期条目自动从任务台删除。
- **右键新增「⏰ 我的提醒」**：对话框实时显示每条提醒内容和剩余时间
  （1 秒刷新），可选中删除，到点自动消失。

### 验证

- 新增/改写 test_agent_features.py 用例：复合时长解析 4 组、seconds 归一化、
  计划确认流（待确认→确认→打勾→完成播报→复位）、执行循环（自动推进/
  无动作沉降）、PlanCard 信号与渲染、重复动作去重、send_command 取消
  计划、remove_reminder、format_duration —— 共 **97 项全过**；
- 老用例回归全过（doc_generator / doc_edit / settings_persistence /
  power / volume）；
- 自测增至 **25 项**，真机 25/25 全过（新增提醒秒数归一化、复合时长换算
  2 项）；离屏环境仅剩已知的设置面板尺寸 2 项环境性失败。
- 按此前约定本轮不重新打包 exe（源码已就绪，下次打包即含全部新功能）。

### 十八、气泡加大（09-19，用户反馈）

- 气泡文本区最大宽度 340→480（与历史记录面板 560 宽的消息区同档），
  内边距 14/9→16/11，字号 16px→18px；短文本不换行、长文本限宽换行的
  手动尺寸计算逻辑不变（fontMetrics 自动吃到新字号）。

### 十九、任务计划卡放大（09-19，用户反馈）

- 上轮理解偏差把用户气泡放大了，本轮已回退（气泡恢复 340 宽/16px）。
- 任务计划卡按历史记录面板规格放大：宽 236→560（与面板同宽），标题
  13→17px、步骤/按钮 12→15/14px、行距与内边距同步放宽；窄屏上贴边
  放不下时自动收进屏幕内。

### 二十、计划卡再放大 + 修背景透明（09-19，用户反馈）

- 修 bug：计划卡背景整体透明只剩文字——自定义 QWidget 子类的顶层窗口
  上画样式表底色不可靠（WA_StyledBackground 也不生效）。改为与气泡同款
  结构：外层透明窗口承载圆角，内层普通 QWidget 面板（#planPanel）画
  深色底，离屏抓像素验证中心 rgba(24,28,42,242)、四角透明圆角。
- 再放大一倍：宽 560→1080（超出窄屏自动夹到屏宽-48），标题 17→26px、
  步骤 15→22px、按钮 14→18px、行距/内边距同步翻倍。

### 二十一、计划全自动执行（修卡死）+ PPT 多模板/表格（09-19，用户反馈）

用户实测计划执行"卡死"、且每步都要人说"继续"。根因与修复：

- **卡死根因**：计划轮请求在途时用户一说话，send_command 会打断该请求，
  但 `_plan_round_pending` 标记残留为 True——之后自动推进和「继续」全被
  这个死标记挡住，计划永远停摆。现在打断在途请求时同步清掉
  `_plan_round_pending` 与 `_suppress_fallback`。
- **全自动执行**：确认后不再任何一步交还用户——每轮做完自动开下一轮；
  模型只说话没调工具的轮次视为"口头完成当前步骤"推进一格，保证计划
  总能自动走完；PLAN_MAX_ROUNDS 8→15 作安全阀，耗尽才汇报进度交还；
  计划轮提示词明确"不要停下来询问用户要不要继续"。
- **PPT 五套模板**（doc_generator 重写 PPT 区）：深空蓝/翡翠绿/暖橙/
  绛紫/雅灰，各有独立配色 + 封面装饰风格（竖色条/底部色带/大圆/斜带/
  细线框）。模型在 content.template 点名（中英文别名都认），没给就按
  标题关键词猜，再退默认。
- **PPT 原生表格**：slide 支持 table 字段（headers/rows），渲染成
  python-pptx 原生表格——主题色表头白字加粗、隔行浅色填充、列宽按内容
  加权、单元格垂直居中，可在 PPT 里继续编辑；要点+表格同页上下排，
  行数上限 10（同页带要点时 7）防撑爆版面。
- **排版精修**：内容页标题 28pt、上下呼应双色条、页码改为主题色圆徽章、
  要点改主题色 ▪ 起头；新增自动"谢谢观看"结束页（回扣封面风格）。

### 验证

- test_doc_generator 新增模板/表格段：模板库五套、别名/关键词/默认解析、
  表格行列与表头主题色回读、行数裁剪、五模板全量渲染、结束页文本 ——
  全套通过（页数断言同步 +1 结束页）；
- test_doc_edit / main 自测的夹具页数断言同步修正；test_agent_features
  改写计划循环段（全自动完成播报 + 打断后可重新推进）—— 97 项全过；
- 老用例回归全过（doc_generator / doc_edit / settings_persistence /
  power / volume）；自测真机 **25/25**。

### 二十二、PPT 彩色版式 + 内容量加厚（09-19，用户反馈）

用户问"PPT 底色只有白色吗，是不是 DeepSeek 的原因"——不是：模型只出
文字内容，配色版式全部由本地 doc_generator 渲染；之前内容页设计成
浅色近白 + 色条，观感确实发白。本轮连同"内容太少"一起修：

- **彩色内容页版式**：内容页底色改用各模板的浅色调（不再是白纸），
  四角加主题色装饰圆，正文装进白色圆角内容卡（细描边）——彩色边框
  层次 + 白卡正文，封面深底/内容彩底/结束页深底三段呼应。
- **提示词强制内容量**：PPT 正文 6~10 页、每页 3~6 条要点、每条
  12~24 字完整陈述句且尽量带数字/对比/具体举措，禁止干瘪短语和空话；
  数据类内容至少 1~2 页用表格；文档请求 max_tokens 下限 4096→6144。
- **离线示例同步加厚**：mock 的 PPT 示例扩到 6 页、要点写成完整句子、
  含 2 页表格，离线演示也能看到新排版效果。

### 验证

- 冒烟：内容页底色/圆角卡/装饰圆逐形状回读确认；五模板渲染正常；
- 老用例回归全过（doc_generator / doc_edit / agent_features 97 项 /
  settings_persistence / power / volume）。

## 二十三、回复气泡加大加字（2026-09-19）

用户反馈：皮皮回答问题时头顶的回复气泡偏小，要求气泡加大一点、里面的字也放大。

### 改动

- `ui/ui_manager.py`：`BubbleWidget.MAX_TEXT_WIDTH` 340→430（长回复
  换行宽度上限加 26%），面板内边距 14/9→18/13（`_pad_lr`/`_pad_tb`
  同步改为 36/38），气泡整体更宽松。
- `ui/style.qss`：`#bubbleLabel` 字号 16px→18px（微软雅黑），
  `#bubblePanel` 圆角 14px→16px 配合新尺寸。
- 尺寸手动计算逻辑不变：真机验证长文本标签宽 416（旧上限 340）、
  `QFontInfo` 确认 18px 生效。

### 验证

- test_agent_features 97/97；main.py --selftest 离线 23/25（仅剩两个
  已知离线环境项，真机 25/25）。

## 二十四、待办独立面板 + 历史记录框再放大（2026-09-19）

用户需求：①待办事项单列一个右键入口，面板字大框大看得清；②历史记录
框长宽约 1.5 倍、字体同步放大。

### 待办面板

- `ui/pet_window.py`：右键菜单在「⏰ 我的提醒」后新增「📋 我的待办」；
  `_show_todos()` 弹 780×660 大面板，列表字号 22px、条目行高加大，
  ○/✅ 区分未完成/已完成，空态显示占位提示；按钮「✔ 完成选中 /
  🗑 删除选中 / 🧹 清理已完成 / 关闭」；2 秒定时刷新（皮皮随时可能
  加待办），刷新保留选中行；每条用 UserRole 记 (列表序号, 未完成序号)，
  完成/删除按正确映射落库。
- `framework/task_board.py`：新增 `remove_todo(index)`（1 起始，越界
  返回 None），与 `remove_reminder` 对称。

### 历史记录框 ×1.5

- `ui/input_dialog.py`：`PANEL_WIDTH/HEIGHT` 560×640→840×960；
  会话头行高 30→36；名称行 26→30、条目留白 18→20；头像 22→28，
  正文宽让出对应空间；`_content_height` 兜底估算按 19px 字号重调
  （字符宽 10px、行高 27、气泡内边距 20px）。
- `ui/style.qss`：历史区全部字号上调——正文/列表 15→19px、昵称
  14→18px、时间/会话头/计数/清空 13→16px、小按钮 12→15px、标题
  17→22px、提示 12→15px、关闭 15→19px；正文气泡 padding 6px 10px
  →9px 13px。

### 验证

- 离屏交互脚本：完成/删除/清理/空态占位全过；真机确认待办列表
  22px、历史列表 19px、面板 840×960；
- test_agent_features 97→99 项（补 remove_todo 两项）全过；
  main.py --selftest 离线 23/25（仅剩两个已知离线环境项）。

## 二十五、视觉模型接入 glm-4.6v-flash + 视觉配置可自填（2026-09-19）

用户想把视觉模型换成智谱新的免费视觉推理模型 glm-4.6v-flash
（GLM-4.6V 的免费版，128K 上下文，思考模式可开关），并确认了
BaseURL/模型名的接入疑问。

### 改动

- `framework/config.py`：默认视觉模型 glm-4v-flash→glm-4.6v-flash；
  视觉三项抽出厂默认常量 VISION_URL_DEFAULT / VISION_MODEL_DEFAULT /
  VISION_KEY_DEFAULT，apply_user_settings 回退一律指向常量——
  修掉"清空输入框回不到默认、而是保留上次自定义值"的缺陷。
- `framework/core.py`：视觉请求体抽成模块级 build_vision_payload，
  GLM 推理系列（模型名含 4.6v/thinking）显式附带
  thinking={"type":"disabled"}——"看屏幕"要快答，且避免思考内容
  混入；解析端 content 为空时兜底取 reasoning_content。
- `ui/ui_manager.py`：设置面板视觉 Key 下新增「视觉地址」「视觉模型
  名」两个选填框（留空=出厂默认），vision_key_changed 信号升级为
  vision_config_changed(key,url,model)。
- `ui/pet_window.py`：_set_vision_key → _set_vision_config，三项一起
  写 config 并 save_user_settings(vision_key/vision_url/vision_model)。

### 用法

- 什么都不填只用主 AI：视觉功能不启用；
- 只填视觉 Key（智谱开放平台申请）：即用 glm-4.6v-flash 看屏幕；
- 想换其他 OpenAI 兼容视觉模型：三个框都填对应值即可。

### 验证

- settings_persistence 15/15（新增默认值检查 + 三项持久化回环 +
  测后恢复原值，恢复检查曾抓出回退缺陷）；
- test_agent_features 103/103（新增 build_vision_payload 4 项：
  4.6v/4.1v-thinking 关思考、其他模型不带该参数）；
- test_settings_dialog 真机 ALL TESTS PASS（新输入框未挤爆面板）；
- 离屏链路脚本：填值原样发出、清空回出厂默认；
- main.py --selftest 离线 23/25（仅剩两个已知离线环境项）。

## 二十六、智能体赛道补强：联网检索 + 读文档 + 交付面板 + 网页报告 + 工作日志（2026-09-19）

对照「AI+超级智能体：自主智能体，人人可用的数字员工」赛道主题补齐
两大能力空洞（检索增强、文档理解）和三项数字员工体验。

### 联网检索（web_search 动作）

- 新增 `framework/web_search.py`：智谱 web-search-pro 封装
  （POST /api/paas/v4/tools），`parse_search_payload` 模块级容错解析
  （tools 字段 / content 序列化 JSON 两种历史格式都兼容），Key 用户自填
  （config: WEBSEARCH_API_KEY/URL，持久化 websearch_key/websearch_url）。
- `core.py`：`_SearchWorker` 后台线程；检索成果写入「资料缓冲」
  `_reference_notes`（上限 6 条/单条 800 字），随 AIWorker 的 notes 参数
  注入系统提示词（【检索资料】块）。单发搜索完成后自动开跟进轮让 AI
  基于资料回答；计划执行轮次资料跨轮保留——"调研→出PPT"由此成链。
  用户发起新话题时缓冲清空。无 Key 友好降级。

### 读文档（read_doc 动作）

- 支持 txt/md/csv/json/log/py 直接读、docx/pptx 走 extract_content、
  xlsx 走 openpyxl（上限 READ_DOC_TEXT_MAX=6000 字）；内容同样入
  资料缓冲 + 跟进轮总结/答问；读文档记入工作日志。

### 网页报告（create_doc html）

- `doc_generator.py`：DOC_TYPE_EXTS/normalize 加 html（别名 网页/htm/web）；
  `_gen_html` 生成单文件 HTML 报告（深空蓝渐变头 + 卡片章节 + 主题色表格，
  html.escape 转义），生成成功后 `os.startfile` 自动用浏览器打开验收；
  保存对话框/类型标签同步支持。

### 交付面板与工作日志

- `task_board.py`：新增 deliveries（按路径去重、上限 100）与 worklog
  （上限 300、`today_worklog()` 按本地日切过滤），同存 tasks.json。
- `core.py`：文档生成成功 → 记交付 + 记日志（并播报"网页报告/PPT…"）；
  完成/搁置计划、设提醒、记/办待办、读文档、搜索均记日志；
  work_log 动作卡片播报今日 8 条（"你今天干了啥"）。
- `pet_window.py`：右键菜单新增「📦 我的交付」（双击/按钮 os.startfile
  打开、删除记录）与「📔 工作日志」（流水展示、清空），与待办面板同一套
  大字样式（_TASK_PANEL_QSS）；设置面板新增「搜索Key」输入框
  （websearch_key_changed 信号 → 持久化）。

### 测试防污染

- agent/doc_generator/doc_edit/selftest 的 Framework 级测试统一把
  fw.tasks._file 指到临时文件并清空四类数据——修复"测试把交付/日志写进
  真实 tasks.json"的污染（已清理残留），日志钩子顺序 bug（wait 未定义）修复。

### 验证

- test_agent_features 103→129 项（动作解析/搜索双格式解析/资料缓冲/
  mock 分支/无Key降级/read_doc 集成/交付日志 CRUD）全过；
- test_doc_generator 加 html 生成/转义/别名/缺内容报错，全过；
- test_settings_persistence 15→17 项（搜索Key回环）；
- selftest 27/29（新增 网页报告/检索解析/交付/日志 4 项，仅剩 2 个
  已知离线环境项）；power/volume ALL PASS；
- 交付/日志面板离屏交互脚本全过。

## 二十七、2026-09-19 历史折叠 + 动态重规划 + 工作流模板 + 稳定性加固

### 历史记录折叠（长回复不再裁剪、可展开）

- `input_dialog.py`：超过 `FOLD_TEXT_LEN=140` 字的记录默认只显示开头
  +「▸ 展开全文」按钮，点击原位重建该行（`_replace_row`，行高重算、
  scrollToItem 定位），按钮变「▴ 收起」；展开状态存 `_expanded`（按
  (轮次, 角色)），刷新/新消息不丢。双击重发、复制、删除始终用完整原文。
- 修复"长回复只显示一部分"的根因：条目高度此前按未 polish 的默认字体
  度量（QSS 19px 未生效），`_content_height` 改为先 `ensurePolished()`
  再用 fontMetrics.boundingRect(TextWordWrap) 精确计算。

### 动态重规划（计划步骤受阻自动调整）

- `config.py`：`PLAN_MAX_REPLANS = 2`（重规划预算，防循环重试）。
- `core.py`：新增 `_note_plan_failure(reason)`——计划执行中工具动作失败
  （找不到应用/打不开/文档生成失败或被拒/搜索无果或无Key/文件缺失或
  格式不支持/视觉失败）登记受阻原因；本轮动作收口时若在预算内 →
  `_plan_replan()` 把进度+受阻原因发给 AI，要求只返回调整后的
  show_plan；`_do_show_plan` 对重规划轮自动接续（替换剩余步骤、
  PlanCard.update_steps 刷新卡片、不走确认卡），按新计划继续无人值守；
  预算用尽则气泡交还用户（"说「继续」我再想办法"）。重规划轮未返回
  show_plan 时按原计划继续并复位 `_plan_auto_plan`；取消/确认/完成/
  搁置/新输入打断均清理重规划标记。
- `plan_card.py`：新增 `update_steps()`（重规划后换步骤、保持执行中态）。

### 工作流模板（一键多步编排）

- `config.py`：`WORKFLOW_TEMPLATES` 4 套——每日科技早报 / 市场调研简报 /
  周报整理 / 会议准备清单（name+desc+command）。
- 新动作 26 `run_workflow(name)`：parser 校验（缺 name 剔除、name 别名
  容错）；`_do_run_workflow` 登记模板命令到 `_workflow_start`，本轮动作
  队列收口后由 `_run_next_action` 作为新任务调 send_command 走完整链路
  （AI 拟计划 → 计划卡确认 → 逐步执行 → 交付）；未知模板礼貌列出可选。
- 提示词：动作池 26 号 + 使用规则 + `_messages` 注入【工作流模板】清单；
  离线 mock：「执行工作流…」拆成 show_plan 等确认（放最前防与搜索等
  分支误触、防自触发死循环）；点名模板名则给 run_workflow。
- `pet_window.py`：右键菜单新增「🚀 工作流模板」面板（大字样式，选中/
  双击运行 → send_command(模板命令)）。

### 稳定性加固（修复执行计划时闪退）

- 根因：PyQt5 对槽函数/线程里未捕获异常默认 qFatal 直接 abort 进程。
  实验证实装自定义 `sys.excepthook` 后进程不再 abort → `main.py`
  `install_crash_guards()` 装 sys.excepthook + threading.excepthook
  （只记日志不崩），独立进程验证通过。
- `core.py` 动作链三层兜底：`_run_next_action` 最外层 try/except；
  单动作分发包 try/except（出错只跳过该动作、气泡提示、队列继续）；
  `_finish_action` 加 `_finish_done` 双收尾防重；`_on_ai_finished`
  整体兜底（出错复位状态并提示"再说一次"）；show_bubble/set_pose/
  show_card/set_emotion 四个 UI 回调吞异常（防窗口销毁后定时器回调
  RuntimeError）；`_DocWorker.run` 包 try（渲染库崩溃转失败信号）；
  `Framework.shutdown` 分步保护。
- `tools/test_agent_stability.py`（新，33 项）：异常兜底/双收尾/UI 回调
  保护/独立进程闪退探针/重规划全链路（登记→触发→自动接续→预算用尽→
  无计划降级）/工作流（解析/模糊匹配/登记/收口启动）/历史折叠（折叠、
  展开、收起、刷新保持、短文不折叠）。settings_persistence 的搜索Key
  断言改为"恢复原值"（本机已存真实 Key 时不再误报）。

### 验证

- test_agent_stability 33/33；test_agent_features 129/129；
  doc_generator / doc_edit / settings_persistence 17/17 / power / volume
  全过；selftest 29/31（仅剩 2 个已知离屏环境项）。

## 二十八、数据分析动作 + 依赖清单与失效单测修复（2026-10-20）

对照简历做了一次代码级自查，挑出"写了但没实现"和"实现了但跑不通"的
三处，本轮全部补掉。

### 数据分析（动作池 26 → 27）

- `framework/data_tool.py`（新）：pandas 读 csv/tsv/xlsx（utf-8-sig → gbk
  编码回退，engine=python 自动嗅探分隔符，行数上限 `DATA_MAX_ROWS`）；
  产出规模/缺失/数值列分布/类别频次/相关性配对/可选分组名次的**结论式
  摘要**——`fit()` 按整行裁剪到 `DATA_SUMMARY_MAX=740`，刻意小于
  `REFERENCE_NOTE_MAX`，保证整段进资料缓冲不被硬截（read_doc 的 6000 字
  以前会被这里截掉，是既有不一致，本次新代码不再踩）。
- matplotlib 先 `use("Agg")` 再 import pyplot（不跟 Qt 抢 GUI 线程），
  中文字体按 微软雅黑/黑体/PingFang 回退；柱状/折线/饼图存
  `data/analysis/`，出图失败只丢图、统计结论照常交付。
- `analyze_data` 进 VALID_ACTIONS（必填 path），参数走既有别名容错
  （文件/路径/完整路径、分组/分组列、图表/图类型、x/y/横轴/纵轴）；
  "饼图/折线/bar/true/false" 由 `normalize_chart_kind` + `chart_wanted`
  归一，不再靠模型恰好写英文枚举。
- `core._do_analyze_data`：摘要进资料缓冲、图表进交付面板+卡片、记工作
  日志，复用 `_search_followup` 让模型讲结论；看门狗预算 150s。
  坏文件/缺文件走 sweat 表情 + 友好气泡 + `_note_plan_failure`。

### 依赖清单与失效单测

- `requirements.txt` 补 `langchain-chroma`、`langchain-huggingface`：
  `rag_engine.py` 直接 import 它们但清单里没有，干净安装（含 Docker
  构建）会把 RAG 装成 ImportError。
- `test_api.test_ingest_text` 原先 monkeypatch 不存在的 `srv.get_rag`
  → 改打 `rag_client.add_text / count` 模块函数（服务端真实调用路径）；
  `test_parser_and_gate.test_search_ok` 样本文本只有 46 字，低于
  `assess_search` 的 60 字门槛 → 补长。两处都是测试写错，不是实现错。
- `tests/test_data_tool.py`（新，20 项）：读数/编码回退/坏格式/空表、
  统计关键数字、分组与零和兜底、模糊列名、长度裁剪、图表类型归一、
  出图与关图、动作参数别名与缺参剔除；`test_agent_features` 增 7 项
  覆盖 analyze_data 的真实执行链。

### 验证

- pytest 19 项（2 failed）→ **39 项全过**；test_agent_features
  129 → 136/136；stability 33/33、power、settings_dialog、
  settings_persistence 全过。
- 动作分发表重扫：27 个动作**全部**有执行分支，missing 为空。
- 环境相关（非本轮引入）：doc_edit / doc_generator 的 PASS 行含 emoji，
  GBK 控制台会 UnicodeEncodeError 中断，需 `PYTHONIOENCODING=utf-8`；
  test_volume 的"命中时返回被调的应用名"要有活动音频会话；
  selftest 仍是那 2 个已知离屏几何项。
- README / 产品功能介绍的动作数改到 27，README 的 `/ingest` 改成实际的
  `/ingest/text`、`/ingest/file`。
