# Linux 原生移植设计文档

## 概述

目标：让一条龙在 Linux 上原生运行，游戏经 Steam + Proton 启动。仅支持**前台模式**；
后台模式（`WM_ACTIVATE` + `PostMessage`，见 [后台模式设计](background_mode_design.md)）依赖 Windows
消息模型，Linux 无等价物，明确不移植。

范围取舍：

| 功能 | Linux 处置 | 原因 |
|------|-----------|------|
| 前台自动化（截图/识别/点击/按键） | ✅ 移植 | 核心链路，Phase 0 已实机验证可行 |
| CUDA 推理（YOLO/OCR） | ✅ 移植 | provider 选择逻辑本就 CUDA 优先，零代码改动 |
| 声音闪避检测 | ✅ 移植 | soundcard PulseAudio 后端可用，仅需守卫 mediafoundation import |
| 后台模式 | ❌ 不移植 | Windows 消息模型专属 |
| 虚拟手柄（vgamepad/ViGEm） | ⏸ 延后 | Windows 驱动专属；未来可用 evdev/uinput 重写 |
| Overlay 叠加层 | ❌ 非 Windows 禁用 | 全套 win32（点击穿透/防截图/GetAsyncKeyState），且属测试功能 |
| Auto-HDR 注册表 | ❌ 非 Windows no-op | Linux 无此概念 |
| BitBlt / PrintWindow 截图 | ❌ 非 Windows 不注册 | Windows GDI 专属 |
| 安装器 / exe launcher / UAC | ❌ 不移植 | Linux 直接 `uv run` 跑源码 |

## Phase 0 实机验证结论（2026-07-08，全部通过）

验证环境：Arch Linux + Hyprland（Wayland）+ RTX 5070 Ti + Steam 原生 + Proton 11.0。
验证脚本：`.debug/temp/linux_spike/`（PEP 723 独立环境，不依赖主项目 venv）。

| # | 项目 | 结论 | 确定的技术路线 |
|---|------|------|---------------|
| 1 | 截图 | ✅ | mss 在 Wayland 下 `XGetImage failed` 不可用；**XComposite `name_window_pixmap`** 抓窗口内容 20–27ms/帧，窗口被遮挡或在其他 workspace 也能抓 |
| 2 | 输入 | ✅ | XTEST 不可靠（Hyprland 不同步 XWayland X 叠放序，指针事件会落到隐藏窗口）；**uinput 虚拟设备**为正解，实测点击/W 前进/镜头转动均在游戏内生效 |
| 3 | 音频 | ✅ | `get_microphone(id=speaker.name, include_loopback=True)` 在 PipeWire-pulse 下**原样可用**（monitor source 自动匹配） |
| 4 | CUDA | ✅ | onnxruntime-gpu 1.27 CUDA EP 在 Blackwell (sm_120) 正常，纯 pip 库自足无需系统 CUDA |

### 输入注入细节（Phase 0 踩坑结论）

- 绝对定位：`hyprctl dispatch movecursor x y`（零误差）+ uinput `BTN_LEFT`。
  纯 uinput `EV_REL` 相对移动受指针加速度影响会过冲，精确定位需闭环校正
  （读 `hyprctl cursorpos` 补偿）或对虚拟设备设 `accel_profile flat`。
- 镜头转向：uinput `EV_REL` 相对移动（无需精确像素数，游戏按增量转视角）。
- uinput 输入按**可见画面**投递：操作前必须 `hyprctl dispatch focuswindow` 把游戏带到前台
  ——语义上正好对应 Windows 前台模式的 `active()`。
- XWayland 侧 X 键盘焦点可能落在 Wine 辅助小窗上，XTEST 键盘同样不可靠，键盘也走 uinput。

### CUDA 依赖组合（NVIDIA 包名新旧混杂，实测可用组合）

```
onnxruntime-gpu>=1.21
nvidia-cuda-runtime / nvidia-cuda-nvrtc / nvidia-cublas / nvidia-cufft / nvidia-curand   # 已改无后缀新名
nvidia-cudnn-cu13                                                                        # cudnn 仍用 cu13 后缀（无后缀是旧占位包）
```

import onnxruntime 前需把 `nvidia` 命名空间下 `**/lib/*.so*` 以 `RTLD_GLOBAL` 预载（两轮，
解决加载顺序依赖）；新版 wheel 装在 `site-packages/nvidia/cu13/lib/`。

## Steam + Proton 环境事实

- 启动链：`steam://rungameid/<appid>` → **HYP.exe**（HoYoPlay，窗口标题 `Zenless Zone Zero` 带空格）
  → 点「开始游戏」→ 游戏本体窗口。
- 游戏本体窗口标题 **`ZenlessZoneZero`（无空格）**，与 `zzz_context._get_win_title()` 非 CN 区服
  默认值完全一致；X class `steam_app_<appid>` 可作更稳的备用匹配键。
- 游戏原生全屏 1920x1080，项目 1080p 坐标基准直接成立。
- 登录由 Steam 版自理（Proton 下 HoYo 账号正常），移植版无需实现账密输入。
- `OpenGame` 流程改造：Linux 分支 = 起 `steam steam://rungameid/<appid>` → 等 HYP 窗口 →
  OCR 点「开始游戏」→ 等游戏本体窗口（现有 `wait_game` 纯靠窗口标题轮询，逻辑可沿用）。
  Unity 启动参数改由用户填 Steam 启动选项。

推荐使用流程（Linux）：**用户手动启动游戏，进入后再运行工具**——工具检测已运行的游戏窗口并自动化，
这条路径 100% 可靠（Phase 2/3 已验证）。自动启动（steam→HYP→自动点击）为 best-effort：
HYP 窗口在场时自动点击可正常启动游戏（已验证），但 Steam/HYP 冷启动时序不稳定
（窗口闪现、关闭后 Steam 短时间仍认为 app 运行而忽略 rungameid），故 `wait_game` 自动点击失败时
不硬失败，会记录提示让用户手动点「开始游戏」并继续等待游戏本体窗口。
另注：`close_game` 在 Linux 上关闭游戏后，Steam 可能短时间拒绝 rungameid 重启，重启型自动化不可靠。

HYP 启动器点击的关键坑（Phase 4 踩坑）：
- HYP 是 CEF/Chromium 网页 UI，读 XWayland 绝对指针位置。`hyprctl movecursor` 只移动合成器光标、
  **不同步 XWayland 指针**，故对 HYP 点击无效（对 Unity 全屏游戏本体有效）。统一解法：`move_to`
  改用 uinput 相对移动闭环（产生真实指针事件，两者都吃），小步（≤12px）规避指针加速度过冲。
- HYP 内容固定 1280×768 不随窗口缩放，窗口过小时按钮被裁切，需先 `fullscreen 1` 放大再 OCR。

## 平台抽象设计

原则：先抽接口再写实现，Windows 实现保持原行为不变，降低与上游合并的冲突面。

| 抽象点 | 现状 | 改造 |
|--------|------|------|
| 窗口管理 `PcGameWindow` | 纯 win32（HWND/GetClientRect/ClientToScreen），顶层 import 即炸 | 抽 `GameWindow` 接口（init/is_valid/is_active/active/win_rect/game2win_pos），Linux 实现走 X11 EWMH（python-xlib）+ compositor adapter 激活 |
| 截图后端 | 4 后端注册，GDI 系顶层依赖 win32 | 非 Windows 只注册 mss/pil + 新增 **XCompositeScreencapper**；优先序 `xcomposite → mss → pil` |
| 输入 `PcControllerBase` | 后台 win32 调用内联在业务方法里 | 抽前台/后台策略；Linux 仅前台策略：uinput 键鼠 + compositor adapter 光标定位 |
| compositor adapter | 无 | 新增接口（激活窗口/设光标/查几何），首版仅 Hyprland（hyprctl）实现；其他合成器后续按需扩展 |
| 剪贴板 `PcClipboard` | win32clipboard | Qt `QClipboard` 或 wl-clipboard |
| 推理 | provider 已 CUDA 优先、DML 有守卫 | 仅改依赖声明 + CUDA 库预载 |
| 音频闪避 | 写死 `soundcard.mediafoundation` import | 平台条件 import，其余原样 |

依赖矩阵（pyproject）：`environments` 加 linux；`pywin32`/`onnxruntime-directml`/`pyuac`/`vgamepad`
加 `sys_platform == 'win32'` marker；linux 侧加 `onnxruntime-gpu` + NVIDIA pip 库 + `python-xlib` +
`evdev`；移除死依赖 `gensim`（全仓库零引用）。

Phase 1 已落地的要点：
- CUDA 预载：`one_dragon/utils/ort_preload.py`，在 `onnx_model_loader.py` 与
  `onnxocr/inference_engine.py` 的 `import onnxruntime` 之前 import 生效。
- 依赖坑：pyautogui→mouseinfo 在 Linux 拉取古老 `python3-xlib`(0.15)，与 `python-xlib` 模块同名
  冲突，已用 `[tool.uv] override-dependencies` 屏蔽（`python3-xlib ; sys_platform == 'win32'`）。
- 顶层 import 平台守卫：`pc_game_window` / `pc_controller_base` / `pc_clipboard` / `debug_utils` /
  `overlay/utils/win32_utils` / `auto_hdr` / `gui/app.py`（GDI 截图后端与 `app_utils` 的 win32
  调用均在 try 内延迟求值，无需守卫；`backend_keyboard_mouse_contoller.py` 为零引用死文件未处理）。
- Linux 启动方式：`PYTHONPATH=src QT_QPA_PLATFORM=xcb uv run python src/zzz_od/gui/app.py`
  （强制 xcb 让 GUI 走 XWayland，与窗口/截图/输入链路同一坐标世界）。

## 阶段计划

| 阶段 | 内容 | 里程碑 | 状态 |
|------|------|--------|------|
| 0 | 四项技术验证 spike | 全部通过 | ✅ 2026-07-08 |
| 1 | 依赖矩阵 + win32 顶层 import 解炸 | GUI 在 Linux 启动 | ✅ 2026-07-08 |
| 2 | X11 窗口层 + XComposite 截图后端 | 截到游戏画面，OCR/YOLO 识别跑通 | ✅ 2026-07-08 |
| 3 | uinput 输入层（前台策略） | controller 层激活/点击/按键经 OCR 验证 | ✅ 2026-07-08 |
| 4 | Steam+Proton 启动集成（platform=STEAM、HYP 点击、auto_hdr no-op） | 自动启动 best-effort，手动启动为推荐流程 | ✅ 2026-07-08 |
| 5 | 音频闪避后端守卫 + 实时推理 | 音频 loopback + flash 分类器 CUDA 已验证；完整战斗内闪避待用户实战验证 | ✅ 2026-07-08（部分） |
| 6 | 收尾（env 层 Linux 分支、文档、测试） | | 进行中 |

### Phase 5 说明

- 音频：`auto_battle_dodge_context._record_loop` 的 `soundcard.mediafoundation` import 加平台守卫，
  Linux 走 PulseAudio 后端，`get_microphone(speaker.name, include_loopback=True)` 原样匹配 monitor source（已验证录音循环正常）。
- 实时推理：flash 分类器（YOLOv8-cls）CUDA EP 单帧 ~0.5ms（Phase 0 spike），远快于闪避窗口。
- 完整「战斗内看到闪光/听到音效 → 注入闪避键」的端到端闭环由构成件组合而成（截图/推理/音频/输入均已单独验证），
  建议由用户在真实战斗中运行闪避助手做最终确认。

### Linux 运行方式（MVP）

```shell
uv sync --group dev
# 首次装完若 pyautogui 连带装了 python3-xlib，需修复 python-xlib：
uv sync --reinstall-package python-xlib
PYTHONPATH=src QT_QPA_PLATFORM=xcb uv run python src/zzz_od/gui/app.py
```

推荐流程：先手动启动游戏并进入，再运行工具。需用户在 `input` 组（`/dev/uinput` 可写）。
env 层安装器（uv 自举、python-build-standalone 下载）为 Windows 打包链专属，Linux 直接用 `uv run` 跑源码，不移植。

## 风险与注意

1. **合成器耦合**：激活/光标走 hyprctl，仅 Hyprland；KDE/GNOME 需另写 adapter（KWin scripting / GNOME 无通用接口，属已知限制）。
2. **时序漂移**：operation 等待/重试参数按 Windows 调校，Proton 下加载时间不同，可能需局部调参（长尾）。
3. **上游同步**：平台抽象层越干净合并冲突越少；禁止在业务代码里散落 `if platform` 分支。
4. **uinput 权限**：用户需在 `input` 组（`/dev/uinput` 可写）。
5. 自动化工具的账号风险与 Windows 版相同，Linux/Proton 不改变性质。
