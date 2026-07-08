# Linux 移植 · 改动实施记录

本文件逐文件记录「为让项目在 Linux 原生运行」所做的全部代码改动，作为评审与后续接手的清单。
设计意图与技术验证见 [linux_port_design.md](linux_port_design.md)（本文是「改了哪些文件、怎么改的」，
设计文档是「为什么这么设计」）。

## 改动原则

1. **平台标记而非删除**：Windows 专属依赖用 `; sys_platform == 'win32'` 标记，Linux 侧 uv 自动跳过；
   不从 `pyproject.toml` 删除。保证项目在 Windows 上照常工作，且与上游合并时冲突最小。
2. **先抽象再分支**：新增 Linux 实现（窗口/截图/输入）尽量走已有抽象基类的新子类或平台工厂，
   而非在业务代码里散落 `if platform`。少量无法抽象的内联点用 `sys.platform` 守卫。
3. **顶层 import 守卫**：所有 `import win32*` / `ctypes.windll` 顶层调用加平台守卫，
   保证 Linux 上 import 链路不崩（这是移植的第一道门槛）。
4. **仅前台模式**：后台模式（`WM_ACTIVATE` + `PostMessage`）依赖 Windows 消息模型，Linux 不移植。

## 新增文件（8）

| 文件 | 行数 | 作用 |
|------|------|------|
| `src/one_dragon/utils/ort_preload.py` | 38 | Linux 下在 `import onnxruntime` 前用 `RTLD_GLOBAL` 预载 pip 版 NVIDIA CUDA 库（`nvidia/**/lib/*.so*`，两轮加载解决顺序依赖）。非 Linux/未装 nvidia 包时 no-op。 |
| `src/one_dragon/base/controller/linux_compositor.py` | 115 | 合成器适配层（当前仅 Hyprland/hyprctl）：按标题激活/关闭/最大化窗口、设/读光标位置、查焦点。是「Wayland 下 uinput 输入按可见画面投递、操作前须激活游戏窗口」的落点。 |
| `src/one_dragon/base/controller/x11_game_window.py` | 201 | `X11GameWindow(PcGameWindow)`：python-xlib + EWMH `_NET_CLIENT_LIST` 按标题找窗、取几何、激活（优先合成器、回退 EWMH）、关闭。替代纯 win32 的 `PcGameWindow`。 |
| `src/one_dragon/base/controller/uinput_input.py` | 285 | 内核级 uinput 输入底层：键位映射（pynput 风格键名→evdev 键码）、键鼠按放、绝对定位（相对移动闭环，小步避加速度过冲）、相对移动（转镜头）、滚轮、文本输入。 |
| `src/one_dragon/base/controller/pc_button/uinput_button_controller.py` | 72 | `UInputButtonController(PcButtonController)`：Linux 前台键鼠实现，键名体系与 `KeyboardMouseController` 一致，底层走 uinput。 |
| `src/one_dragon/base/controller/pc_screenshot/xcomposite_screencapper.py` | 142 | `XCompositeScreencapper(ScreencapperBase)`：XComposite `NameWindowPixmap` 直抓窗口内容（不受遮挡/工作区影响），Linux 首选截图后端。 |
| `docs/develop/one_dragon/linux_port_design.md` | 148 | 移植设计文档（范围取舍、Phase 0 验证、平台抽象、阶段计划、踩坑）。 |
| `run_linux.sh` | 27 | Linux 启动脚本（`--sync` 首次同步、检查 uinput 权限、封装 `PYTHONPATH=src QT_QPA_PLATFORM=xcb`）。 |

## 修改文件（16）

### 依赖与配置

- **`pyproject.toml`**：`[tool.uv] environments` 加 linux；`onnxruntime-directml`/`pywin32`/`pyuac`/`vgamepad`
  加 `; sys_platform == 'win32'`；linux 侧加 `onnxruntime-gpu` + 6 个 NVIDIA pip 库 + `python-xlib` + `evdev`；
  移除死依赖 `gensim`（全仓零引用）；`override-dependencies` 屏蔽 pyautogui 带进来的旧 `python3-xlib`（与 `python-xlib` 撞名）。

### 顶层 import 守卫（Linux 上 import 不崩）

- **`pc_game_window.py`**：`win32ui` / `ctypes.wintypes.RECT` / `pygetwindow.Win32Window` 加 `sys.platform` 守卫 + 占位类。
- **`pc_controller_base.py`**：`win32api/con/gui` 守卫；并新增平台分支（见下）。
- **`pc_clipboard.py`**：`pywintypes` / `win32clipboard` / `win32con` 守卫。
- **`utils/debug_utils.py`**：`win32clipboard` / `win32con` 守卫，`copy_image_to_clipboard` 非 Windows 直接返回 False。
- **`one_dragon_qt/overlay/utils/win32_utils.py`**：模块级 `_user32 = ctypes.windll.user32` 移入 `if win32` 块，
  非 Windows 为 None，各函数入口加 `_user32 is None` 早退（Overlay 仅 Windows）。
- **`zzz_od/gui/app.py`**：启动失败弹窗 `MessageBoxW` 加守卫，非 Windows 打印 stderr。
- **`enter_game/auto_hdr.py`**：`winreg` 守卫，非 Windows 时 `DisableAutoHDR`/`EnableAutoHDR` 直接 round_success；
  顺手 `WindowsError` → `OSError`（Linux 上 `WindowsError` 未定义，是隐患）。

### 平台分支（核心逻辑接入 Linux 实现）

- **`pc_controller_base.py`**（改动最集中）：
  - `game_win` 按平台选 `PcGameWindow` / `X11GameWindow`；
  - `keyboard_controller` 按平台选 `KeyboardMouseController` / `UInputButtonController`；
  - 点击/拖曳/滚动/相对移动/文本输入/取光标位置，各加 Linux 分支走 `uinput_input`；
  - `_ensure_mouse_mode` 非 Windows 直接置键鼠模式；`enable_background_mode` 非 Windows 回退前台。
- **`pc_screenshot/pc_screenshot_controller.py`**：截图后端注册与优先序平台化——Windows 注册 GDI 系（PrintWindow/BitBlt），
  Linux 注册 `XCompositeScreencapper`；优先序 Linux 为 `xcomposite → mss → pil`。
- **`envs/env_config.py`**：`ScreenshotMethodEnum` 加 `XCOMPOSITE`。
- **`zzz_od/controller/zzz_pc_controller.py`**：`move_mouse_relative` 加 Linux 分支走 `uinput_input.move_relative`（转镜头）。
- **`zzz_od/context/zzz_context.py`**：`_get_win_title` 非 Windows 固定返回 `ZenlessZoneZero`（Steam 国际服）。

### Steam + Proton 启动

- **`zzz_od/const/game_const.py`**：新增 `STEAM_APP_ID = '4162040'`。
- **`enter_game/open_game.py`**：`open_game` 非 Windows 走 `_open_game_by_steam`（`steam steam://rungameid/...`）；
  `wait_game` 非 Windows 时 best-effort 调 `_linux_click_hyp_start`（找 HYP 启动器窗口→必要时最大化→OCR 定位「开始游戏」→
  uinput 点击），失败不硬失败、提示手动点击并继续等游戏本体窗口。

### 音频闪避

- **`auto_battle/auto_battle_dodge_context.py`**：`_record_loop` 的 `from soundcard.mediafoundation import SoundcardRuntimeWarning`
  加 Windows 守卫（该后端 Linux 不存在），Linux 走 PulseAudio 后端，其余录音逻辑原样。

### 文档索引

- **`docs/develop/README.md`**：设计文档索引加「Linux 原生移植」链接。

## 验证记录

| 环节 | 方式 | 结果 |
|------|------|------|
| Phase 0 技术验证 | `.debug/temp/linux_spike/` 独立脚本实测 | 截图/输入/音频/CUDA 四项全过 |
| GUI 启动 | `run_linux.sh` | 主窗口正常渲染（唯一 ERROR 是 git 拉取，与移植无关） |
| 截图 + OCR | 实机截 Proton 游戏 | XComposite 17–27ms，OCR 识别正常 |
| 输入链路 | controller 激活→ESC→点击→OCR 校验 | 全程生效 |
| Steam 启动 | OpenGame 实测 | HYP 窗口在场时自动点击成功启动；冷启动时序 best-effort |
| 音频 | loopback 录音循环 | 正常从 monitor 采集 |

## 已知限制 / 未完成

- 后台模式、虚拟手柄（vgamepad）：Linux 不支持（前者无等价、后者需 evdev/uinput 另写）。
- 自动启动为 best-effort，推荐「手动开游戏再跑工具」。
- compositor 适配层当前仅 Hyprland；KDE/GNOME 需另写 adapter。
- 完整战斗内闪避闭环的实战验证待用户在真实战斗中确认（构成件均已单独验证）。
- env 层安装器（uv 自举等）未移植，Linux 直接 `uv run` 跑源码。
