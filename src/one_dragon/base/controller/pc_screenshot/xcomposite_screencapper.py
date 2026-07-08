import sys
import threading

import numpy as np
from cv2.typing import MatLike

from one_dragon.base.controller.pc_game_window import PcGameWindow
from one_dragon.base.controller.pc_screenshot.screencapper_base import ScreencapperBase
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils.log_utils import log


class XCompositeScreencapper(ScreencapperBase):
    """
    Linux(X11/XWayland) 下用 XComposite NameWindowPixmap 直接抓取窗口内容
    不受合成器遮挡/工作区切换影响；Wayland 下 mss 抓屏不可用，此为首选后端
    见 docs/develop/one_dragon/linux_port_design.md
    """

    def __init__(self, game_win: PcGameWindow, standard_width: int, standard_height: int):
        ScreencapperBase.__init__(self, game_win, standard_width, standard_height)
        self._display = None
        self._x11_win = None
        self._redirected: bool = False
        self._lock = threading.RLock()  # Xlib Display 非线程安全，串行化访问

    def init(self) -> bool:
        """初始化 XComposite 截图方法

        Returns:
            是否初始化成功
        """
        self.cleanup()
        if sys.platform != 'linux':
            return False
        try:
            from Xlib import display as x_display
            from Xlib.ext import composite
            with self._lock:
                d = x_display.Display()
                if not d.has_extension('Composite'):
                    d.close()
                    return False
                win_id = self.game_win.get_hwnd()
                if not win_id:
                    d.close()
                    return False
                win = d.create_resource_object('window', win_id)
                composite.redirect_window(win, composite.RedirectAutomatic)
                d.sync()
                self._display = d
                self._x11_win = win
                self._redirected = True
                return True
        except Exception:
            log.debug('XComposite 初始化失败', exc_info=True)
            self.cleanup()
            return False

    def capture(self, rect: Rect, independent: bool = False) -> MatLike | None:
        """截取窗口内容

        Args:
            rect: 截图区域（此后端直接抓窗口内容，仅用于兜底判断）
            independent: 是否独立截图

        Returns:
            RGB 截图数组，失败返回 None
        """
        try:
            if independent or self._display is None:
                return self._capture_independent()
            with self._lock:
                return self._grab(self._display, self._x11_win)
        except Exception:
            # 窗口重建/尺寸变化会使 pixmap 失效，重新初始化再试一次
            if independent:
                return None
            if self.init():
                try:
                    with self._lock:
                        return self._grab(self._display, self._x11_win)
                except Exception:
                    return None
            return None

    def _capture_independent(self) -> MatLike | None:
        """用独立的 X 连接抓取一次"""
        from Xlib import display as x_display
        from Xlib.ext import composite
        win_id = self.game_win.get_hwnd()
        if not win_id:
            return None
        d = x_display.Display()
        try:
            win = d.create_resource_object('window', win_id)
            composite.redirect_window(win, composite.RedirectAutomatic)
            d.sync()
            try:
                return self._grab(d, win)
            finally:
                composite.unredirect_window(win, composite.RedirectAutomatic)
                d.sync()
        finally:
            d.close()

    @staticmethod
    def _grab(d, win) -> MatLike:
        """抓取窗口当前内容并转为 RGB 数组"""
        from Xlib import X
        from Xlib.ext import composite
        geom = win.get_geometry()
        pixmap = composite.name_window_pixmap(win)
        try:
            raw = pixmap.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
        finally:
            pixmap.free()
        data = raw.data
        if isinstance(data, str):
            data = data.encode('latin-1')
        arr = np.frombuffer(data, dtype=np.uint8).reshape(geom.height, geom.width, 4)
        # X 返回 BGRX，转 RGB 并保证内存连续
        return np.ascontiguousarray(arr[:, :, [2, 1, 0]])

    def cleanup(self):
        """清理 XComposite 相关资源"""
        with self._lock:
            if self._display is not None:
                try:
                    if self._redirected and self._x11_win is not None:
                        from Xlib.ext import composite
                        composite.unredirect_window(self._x11_win, composite.RedirectAutomatic)
                        self._display.sync()
                except Exception:
                    pass
                try:
                    self._display.close()
                except Exception:
                    pass
            self._display = None
            self._x11_win = None
            self._redirected = False
