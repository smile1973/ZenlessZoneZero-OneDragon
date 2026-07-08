import json
import os
import re
import shutil
import subprocess

from one_dragon.utils.log_utils import log


def is_hyprland() -> bool:
    """当前是否运行在 Hyprland 合成器下"""
    return bool(os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')) and shutil.which('hyprctl') is not None


def _hyprctl(args: list[str]) -> str | None:
    """执行 hyprctl 命令，失败返回 None"""
    try:
        result = subprocess.run(['hyprctl'] + args, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout
        log.debug('hyprctl 执行失败 %s: %s', args, result.stderr)
        return None
    except Exception:
        log.debug('hyprctl 调用异常', exc_info=True)
        return None


def _title_selector(title: str) -> str:
    """按标题精确匹配的 Hyprland 窗口选择器"""
    return f'title:^({re.escape(title)})$'


def activate_window_by_title(title: str) -> bool:
    """
    把指定标题的窗口带到前台（切换 workspace + 聚焦）
    Wayland 下 uinput 输入按可见画面投递，自动化操作前必须先激活游戏窗口
    :param title: 窗口标题（精确匹配）
    :return: 是否成功
    """
    if is_hyprland():
        out = _hyprctl(['dispatch', 'focuswindow', _title_selector(title)])
        return out is not None and 'ok' in out.lower()
    return False


def close_window_by_title(title: str) -> bool:
    """
    请求关闭指定标题的窗口
    :param title: 窗口标题（精确匹配）
    :return: 是否成功
    """
    if is_hyprland():
        out = _hyprctl(['dispatch', 'closewindow', _title_selector(title)])
        return out is not None and 'ok' in out.lower()
    return False


def maximize_window_by_title(title: str) -> bool:
    """
    聚焦并最大化指定标题的窗口
    （HYP 启动器内容为固定 1280x768 且不随窗口缩放，被平铺裁切时需先放大才能看到按钮）
    :param title: 窗口标题（精确匹配）
    :return: 是否成功
    """
    if is_hyprland():
        if not activate_window_by_title(title):
            return False
        out = _hyprctl(['dispatch', 'fullscreen', '1'])
        return out is not None and 'ok' in out.lower()
    return False


def move_cursor(x: int, y: int) -> bool:
    """
    把光标移动到屏幕绝对坐标（合成器原生能力，零误差）
    :param x: 屏幕 X 坐标
    :param y: 屏幕 Y 坐标
    :return: 是否成功
    """
    if is_hyprland():
        out = _hyprctl(['dispatch', 'movecursor', str(int(x)), str(int(y))])
        return out is not None and 'ok' in out.lower()
    return False


def get_cursor_pos() -> tuple[int, int] | None:
    """
    获取当前光标的屏幕绝对坐标
    :return: (x, y)，失败返回 None
    """
    if is_hyprland():
        out = _hyprctl(['cursorpos', '-j'])
        if out:
            try:
                data = json.loads(out)
                return int(data['x']), int(data['y'])
            except Exception:
                return None
    return None


def is_window_focused_by_title(title: str) -> bool:
    """
    当前聚焦的窗口标题是否为指定标题
    :param title: 窗口标题（精确匹配）
    :return: 是否聚焦
    """
    if is_hyprland():
        out = _hyprctl(['activewindow', '-j'])
        if out:
            try:
                return json.loads(out).get('title') == title
            except Exception:
                return False
    return False
