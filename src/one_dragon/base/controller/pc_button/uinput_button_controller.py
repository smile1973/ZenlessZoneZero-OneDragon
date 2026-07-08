import time

from one_dragon.base.controller import uinput_input
from one_dragon.base.controller.pc_button import pc_button_utils
from one_dragon.base.controller.pc_button.pc_button_controller import PcButtonController


class UInputButtonController(PcButtonController):
    """
    Linux 前台键鼠实现：内核级 uinput 虚拟设备
    XTEST(pynput) 在 Hyprland/XWayland 下不可靠，统一走 uinput
    键名体系与 KeyboardMouseController 一致（pynput 风格）
    """

    def __init__(self):
        PcButtonController.__init__(self)
        self._pressed_keys: set[str] = set()  # 当前按下的键

    @staticmethod
    def _mouse_button_name(key: str) -> str:
        """mouse_left -> left"""
        return key[6:]

    def tap(self, key: str) -> None:
        """
        按一次按键
        :param key: 按键
        :return:
        """
        if pc_button_utils.is_mouse_button(key):
            btn = self._mouse_button_name(key)
            uinput_input.mouse_down(btn)
            time.sleep(self.key_press_time)
            uinput_input.mouse_up(btn)
        else:
            uinput_input.key_down(key)
            time.sleep(self.key_press_time)
            uinput_input.key_up(key)

    def press(self, key: str, press_time: float | None = None) -> None:
        """
        :param key: 按键
        :param press_time: 持续按键时间。不传入时 代表不松开
        :return:
        """
        is_mouse = pc_button_utils.is_mouse_button(key)
        if is_mouse:
            uinput_input.mouse_down(self._mouse_button_name(key))
        else:
            uinput_input.key_down(key)
        if press_time is None:
            self._pressed_keys.add(key)
            return

        time.sleep(press_time)
        if is_mouse:
            uinput_input.mouse_up(self._mouse_button_name(key))
        else:
            uinput_input.key_up(key)

    def release(self, key: str) -> None:
        if key not in self._pressed_keys:
            return
        self._pressed_keys.discard(key)
        if pc_button_utils.is_mouse_button(key):
            uinput_input.mouse_up(self._mouse_button_name(key))
        else:
            uinput_input.key_up(key)

    def reset(self) -> None:
        for key in list(self._pressed_keys):
            self.release(key)
