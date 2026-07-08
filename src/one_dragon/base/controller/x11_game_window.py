import threading

from Xlib import X, display

from one_dragon.base.controller import linux_compositor
from one_dragon.base.controller.pc_game_window import PcGameWindow
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils.log_utils import log


class X11GameWindow(PcGameWindow):
    """
    Linux(X11/XWayland) 的游戏窗口实现
    按标题通过 EWMH _NET_CLIENT_LIST 查找窗口；激活优先走合成器适配层，回退 EWMH
    X11 无客户区/窗口区之分（Proton 全屏游戏无装饰），win_rect 即窗口几何
    见 docs/develop/one_dragon/linux_port_design.md
    """

    def __init__(self,
                 standard_width: int = 1920,
                 standard_height: int = 1080):
        PcGameWindow.__init__(self, standard_width, standard_height)
        self._display: display.Display | None = None
        self._x11_win = None  # Xlib window 资源对象
        self._lock = threading.RLock()  # Xlib Display 非线程安全，串行化访问

    def _get_display(self) -> display.Display:
        if self._display is None:
            self._display = display.Display()
        return self._display

    def _clear_cached_window(self) -> None:
        self._win = None
        self._hWnd = None
        self._x11_win = None

    @staticmethod
    def _get_win_title_of(d: display.Display, win) -> str:
        """读取窗口标题 优先 _NET_WM_NAME(UTF-8) 回退 WM_NAME"""
        try:
            prop = win.get_full_property(d.intern_atom('_NET_WM_NAME'), d.intern_atom('UTF8_STRING'))
            if prop is None or not prop.value:
                prop = win.get_full_property(d.intern_atom('WM_NAME'), X.AnyPropertyType)
            if prop is not None and prop.value:
                value = prop.value
                return value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)
        except Exception:
            pass
        return ''

    def init_win(self) -> None:
        """
        初始化窗口：按标题精确匹配 EWMH 客户端列表
        :return:
        """
        if self.win_title is None:
            return

        with self._lock:
            try:
                d = self._get_display()
                root = d.screen().root
                prop = root.get_full_property(d.intern_atom('_NET_CLIENT_LIST'), X.AnyPropertyType)
                if prop is None:
                    self._x11_win = None
                    self._hWnd = None
                    return
                for wid in prop.value:
                    win = d.create_resource_object('window', wid)
                    if self._get_win_title_of(d, win) == self.win_title:
                        self._x11_win = win
                        self._hWnd = int(wid)
                        return
                self._x11_win = None
                self._hWnd = None
            except Exception:
                log.debug('X11 查找窗口失败', exc_info=True)
                self._x11_win = None
                self._hWnd = None

    def get_win(self):
        """
        返回窗口占位对象（自身）供上层判空与 close() 使用
        """
        if self._x11_win is None:
            self.init_win()
        return self if self._x11_win is not None else None

    @property
    def is_win_valid(self) -> bool:
        """
        当前窗口是否仍然存在
        :return:
        """
        with self._lock:
            if self._x11_win is None:
                self.init_win()
            if self._x11_win is None:
                return False
            try:
                self._x11_win.get_geometry()
                return True
            except Exception:
                self._clear_cached_window()
                return False

    @property
    def is_win_active(self) -> bool:
        """
        是否当前激活的窗口
        :return:
        """
        if self._hWnd is None:
            self.init_win()
        if self._hWnd is None:
            return False
        with self._lock:
            try:
                d = self._get_display()
                root = d.screen().root
                prop = root.get_full_property(d.intern_atom('_NET_ACTIVE_WINDOW'), X.AnyPropertyType)
                if prop is not None and prop.value and int(prop.value[0]) == self._hWnd:
                    return True
            except Exception:
                pass
        # XWayland 下 _NET_ACTIVE_WINDOW 可能不同步，回退询问合成器
        return linux_compositor.is_window_focused_by_title(self.win_title)

    def active(self) -> bool:
        """
        显示并激活当前窗口（Wayland 下等价于切到游戏所在工作区并聚焦）
        :return:
        """
        if self.get_win() is None:
            return False
        if self.is_win_active:
            return True

        if linux_compositor.activate_window_by_title(self.win_title):
            return True

        # 非 Hyprland 时回退 EWMH _NET_ACTIVE_WINDOW（真 X11 会话的窗口管理器可响应）
        with self._lock:
            try:
                from Xlib.protocol import event
                d = self._get_display()
                root = d.screen().root
                ev = event.ClientMessage(
                    window=self._x11_win,
                    client_type=d.intern_atom('_NET_ACTIVE_WINDOW'),
                    data=(32, [1, X.CurrentTime, 0, 0, 0]),
                )
                root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                d.sync()
                return True
            except Exception:
                log.error('激活窗口失败', exc_info=True)
                return False

    def close(self) -> None:
        """
        请求关闭窗口（供 close_game 调用）
        """
        if linux_compositor.close_window_by_title(self.win_title):
            return
        with self._lock:
            try:
                from Xlib.protocol import event
                d = self._get_display()
                root = d.screen().root
                ev = event.ClientMessage(
                    window=self._x11_win,
                    client_type=d.intern_atom('_NET_CLOSE_WINDOW'),
                    data=(32, [X.CurrentTime, 1, 0, 0, 0]),
                )
                root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                d.sync()
            except Exception:
                log.error('关闭窗口失败', exc_info=True)

    @property
    def win_rect(self) -> Rect | None:
        """
        获取游戏窗口在桌面上的位置
        :return: 游戏窗口信息
        """
        with self._lock:
            if self._x11_win is None:
                self.init_win()
            if self._x11_win is None:
                return None
            try:
                d = self._get_display()
                root = d.screen().root
                geom = self._x11_win.get_geometry()
                trans = root.translate_coords(self._x11_win, 0, 0)
                return Rect(trans.x, trans.y, trans.x + geom.width, trans.y + geom.height)
            except Exception:
                log.debug('X11 获取窗口几何失败', exc_info=True)
                self._clear_cached_window()
                return None
