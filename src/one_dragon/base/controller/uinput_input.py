import threading
import time

from one_dragon.base.controller import linux_compositor
from one_dragon.utils.log_utils import log

# uinput 虚拟设备与键名映射按需初始化（evdev 仅 Linux 可用）
_lock = threading.RLock()
_keyboard = None
_mouse = None
_key_map: dict[str, int] | None = None

# pynput 风格键名 -> evdev 常量名（与 pc_button_utils 的键名体系对齐）
_NAME_TO_ECODE_NAME: dict[str, str] = {
    'esc': 'KEY_ESC', 'space': 'KEY_SPACE', 'enter': 'KEY_ENTER', 'tab': 'KEY_TAB',
    'backspace': 'KEY_BACKSPACE', 'delete': 'KEY_DELETE', 'insert': 'KEY_INSERT',
    'home': 'KEY_HOME', 'end': 'KEY_END', 'page_up': 'KEY_PAGEUP', 'page_down': 'KEY_PAGEDOWN',
    'up': 'KEY_UP', 'down': 'KEY_DOWN', 'left': 'KEY_LEFT', 'right': 'KEY_RIGHT',
    'shift': 'KEY_LEFTSHIFT', 'shift_l': 'KEY_LEFTSHIFT', 'shift_r': 'KEY_RIGHTSHIFT',
    'ctrl': 'KEY_LEFTCTRL', 'ctrl_l': 'KEY_LEFTCTRL', 'ctrl_r': 'KEY_RIGHTCTRL',
    'alt': 'KEY_LEFTALT', 'alt_l': 'KEY_LEFTALT', 'alt_r': 'KEY_RIGHTALT', 'alt_gr': 'KEY_RIGHTALT',
    'caps_lock': 'KEY_CAPSLOCK', 'num_lock': 'KEY_NUMLOCK', 'scroll_lock': 'KEY_SCROLLLOCK',
    'print_screen': 'KEY_SYSRQ', 'pause': 'KEY_PAUSE', 'menu': 'KEY_MENU',
    'cmd': 'KEY_LEFTMETA', 'cmd_l': 'KEY_LEFTMETA', 'cmd_r': 'KEY_RIGHTMETA',
    '-': 'KEY_MINUS', '=': 'KEY_EQUAL', '[': 'KEY_LEFTBRACE', ']': 'KEY_RIGHTBRACE',
    ';': 'KEY_SEMICOLON', "'": 'KEY_APOSTROPHE', '`': 'KEY_GRAVE', '\\': 'KEY_BACKSLASH',
    ',': 'KEY_COMMA', '.': 'KEY_DOT', '/': 'KEY_SLASH',
}

_MOUSE_BTN_TO_ECODE_NAME: dict[str, str] = {
    'left': 'BTN_LEFT', 'right': 'BTN_RIGHT', 'middle': 'BTN_MIDDLE',
}


def _build_key_map() -> dict[str, int]:
    """构建 键名 -> evdev 键码 的映射"""
    from evdev import ecodes
    key_map: dict[str, int] = {}
    for ch in 'abcdefghijklmnopqrstuvwxyz0123456789':
        key_map[ch] = getattr(ecodes, f'KEY_{ch.upper()}')
    for i in range(1, 25):
        key_map[f'f{i}'] = getattr(ecodes, f'KEY_F{i}')
    for i in range(10):
        key_map[f'numpad_{i}'] = getattr(ecodes, f'KEY_KP{i}')
    for name, ecode_name in _NAME_TO_ECODE_NAME.items():
        key_map[name] = getattr(ecodes, ecode_name)
    return key_map


def _ensure_devices() -> bool:
    """惰性创建 uinput 虚拟键鼠设备（进程内单例）

    需要 /dev/uinput 写权限（用户在 input 组）
    """
    global _keyboard, _mouse, _key_map
    with _lock:
        if _keyboard is not None and _mouse is not None:
            return True
        try:
            from evdev import UInput, ecodes
            if _key_map is None:
                _key_map = _build_key_map()
            if _keyboard is None:
                _keyboard = UInput(
                    {ecodes.EV_KEY: sorted(set(_key_map.values()))},
                    name='one-dragon-virtual-keyboard',
                )
            if _mouse is None:
                _mouse = UInput(
                    {
                        ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL],
                        ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
                    },
                    name='one-dragon-virtual-mouse',
                )
                time.sleep(0.2)  # 等待合成器识别新输入设备
            return True
        except Exception:
            log.error('创建 uinput 虚拟设备失败，请确认用户在 input 组且 /dev/uinput 可写', exc_info=True)
            return False


def get_key_code(key: str) -> int | None:
    """键名转 evdev 键码，不支持的键返回 None"""
    global _key_map
    if _key_map is None:
        _key_map = _build_key_map()
    return _key_map.get(key)


def key_down(key: str) -> bool:
    """按下键盘按键"""
    if not _ensure_devices():
        return False
    code = get_key_code(key)
    if code is None:
        log.error('uinput 不支持的按键 %s', key)
        return False
    from evdev import ecodes
    with _lock:
        _keyboard.write(ecodes.EV_KEY, code, 1)
        _keyboard.syn()
    return True


def key_up(key: str) -> bool:
    """释放键盘按键"""
    if not _ensure_devices():
        return False
    code = get_key_code(key)
    if code is None:
        return False
    from evdev import ecodes
    with _lock:
        _keyboard.write(ecodes.EV_KEY, code, 0)
        _keyboard.syn()
    return True


def mouse_down(button: str = 'left') -> bool:
    """按下鼠标按键"""
    if not _ensure_devices():
        return False
    from evdev import ecodes
    code = getattr(ecodes, _MOUSE_BTN_TO_ECODE_NAME.get(button, 'BTN_LEFT'))
    with _lock:
        _mouse.write(ecodes.EV_KEY, code, 1)
        _mouse.syn()
    return True


def mouse_up(button: str = 'left') -> bool:
    """释放鼠标按键"""
    if not _ensure_devices():
        return False
    from evdev import ecodes
    code = getattr(ecodes, _MOUSE_BTN_TO_ECODE_NAME.get(button, 'BTN_LEFT'))
    with _lock:
        _mouse.write(ecodes.EV_KEY, code, 0)
        _mouse.syn()
    return True


def move_to(x: int, y: int, tolerance: int = 2) -> bool:
    """光标移动到屏幕绝对坐标

    用 uinput 相对移动闭环逼近：相对移动会产生真实指针事件（XWayland/Wine 可见），
    hyprctl movecursor 只移动合成器光标、不同步 XWayland 指针（对 CEF 类窗口无效），故不用。
    小步移动（<=12px）避开指针加速度过冲，保证收敛。
    """
    if not _ensure_devices():
        return False
    from evdev import ecodes
    last_pos = None
    stuck = 0
    for _ in range(120):
        pos = linux_compositor.get_cursor_pos()
        if pos is None:
            # 无合成器坐标反馈时无法闭环，退回一次性相对移动
            return linux_compositor.move_cursor(int(x), int(y))
        dx, dy = int(x) - pos[0], int(y) - pos[1]
        if abs(dx) <= tolerance and abs(dy) <= tolerance:
            return True
        # 卡住检测：连续多次位置不变说明到达屏幕边界，提前退出
        if pos == last_pos:
            stuck += 1
            if stuck >= 5:
                return abs(dx) <= 12 and abs(dy) <= 12
        else:
            stuck = 0
        last_pos = pos
        # 小步移动，接近目标时进一步减小步长，规避指针加速度
        step = 12 if max(abs(dx), abs(dy)) > 24 else 4
        with _lock:
            if dx != 0:
                _mouse.write(ecodes.EV_REL, ecodes.REL_X, max(-step, min(step, dx)))
            if dy != 0:
                _mouse.write(ecodes.EV_REL, ecodes.REL_Y, max(-step, min(step, dy)))
            _mouse.syn()
        time.sleep(0.006)
    # 未完全收敛但已接近也算成功（按钮通常有足够大的点击区域）
    pos = linux_compositor.get_cursor_pos()
    if pos is not None:
        return abs(int(x) - pos[0]) <= 12 and abs(int(y) - pos[1]) <= 12
    return False


def move_relative(dx: int, dy: int, step: int = 20, interval: float = 0.01) -> bool:
    """相对移动（3D 场景转镜头用），分步发送避免游戏丢事件"""
    if not _ensure_devices():
        return False
    from evdev import ecodes
    remain_x, remain_y = int(dx), int(dy)
    while remain_x != 0 or remain_y != 0:
        sx = max(-step, min(step, remain_x))
        sy = max(-step, min(step, remain_y))
        with _lock:
            if sx != 0:
                _mouse.write(ecodes.EV_REL, ecodes.REL_X, sx)
            if sy != 0:
                _mouse.write(ecodes.EV_REL, ecodes.REL_Y, sy)
            _mouse.syn()
        remain_x -= sx
        remain_y -= sy
        if interval > 0:
            time.sleep(interval)
    return True


def click_at(x: int | None, y: int | None, press_time: float = 0.1, primary: bool = True) -> bool:
    """移动到屏幕坐标并点击；x/y 为 None 时在当前位置点击"""
    if x is not None and y is not None:
        if not move_to(x, y):
            return False
        time.sleep(0.02)
    button = 'left' if primary else 'right'
    if not mouse_down(button):
        return False
    time.sleep(max(0.001, press_time))
    return mouse_up(button)


def scroll_wheel(down: int, x: int | None = None, y: int | None = None) -> bool:
    """滚轮滚动：down 为正向下滚

    Windows 版一次滚动约 8~16 个滚轮刻度，这里对齐取 16
    """
    if x is not None and y is not None:
        move_to(x, y)
    if not _ensure_devices():
        return False
    from evdev import ecodes
    notches = -int(down) * 16
    step = 1 if notches > 0 else -1
    with _lock:
        for _ in range(abs(notches)):
            _mouse.write(ecodes.EV_REL, ecodes.REL_WHEEL, step)
            _mouse.syn()
            time.sleep(0.005)
    return True


def type_text(text: str, interval: float = 0.02) -> bool:
    """输入 ASCII 文本（大写字母/部分符号自动加 shift），仅覆盖账号密码场景的常用字符"""
    shift_chars = {c: c.lower() for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'}
    shift_chars.update({'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6',
                        '&': '7', '*': '8', '(': '9', ')': '0', '_': '-', '+': '=',
                        ':': ';', '"': "'", '<': ',', '>': '.', '?': '/', '~': '`',
                        '{': '[', '}': ']', '|': '\\'})
    for ch in text:
        if ch in shift_chars:
            key_down('shift')
            time.sleep(0.01)
            key_down(shift_chars[ch])
            time.sleep(0.01)
            key_up(shift_chars[ch])
            key_up('shift')
        elif ch == ' ':
            key_down('space')
            time.sleep(0.01)
            key_up('space')
        else:
            code = get_key_code(ch)
            if code is None:
                log.warning('type_text 跳过不支持的字符 %s', ch)
                continue
            key_down(ch)
            time.sleep(0.01)
            key_up(ch)
        time.sleep(interval)
    return True


def reset_devices() -> None:
    """关闭并重建虚拟设备（异常恢复用）"""
    global _keyboard, _mouse
    with _lock:
        for dev in (_keyboard, _mouse):
            if dev is not None:
                try:
                    dev.close()
                except Exception:
                    pass
        _keyboard = None
        _mouse = None
