import os
import subprocess
import sys
import time

from one_dragon.base.operation.operation import Operation
from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from zzz_od.const import game_const
from zzz_od.context.zzz_context import ZContext
from zzz_od.operation.enter_game.auto_hdr import DisableAutoHDR, EnableAutoHDR

# HoYoPlay 启动器窗口标题（Steam 版启动链：steam -> HYP -> 游戏本体）
_HYP_WIN_TITLE = 'Zenless Zone Zero'
_HYP_START_TEXTS = ['开始游戏', '開始遊戲', 'Start Game']


class OpenGame(Operation):

    def __init__(self, ctx: ZContext):
        self.ctx: ZContext = ctx
        Operation.__init__(self, ctx, op_name=gt('打开游戏'),
                           need_check_game_win=False)

    @operation_node(name='打开游戏', is_start_node=True, screenshot_before_round=False)
    def open_game(self) -> OperationRoundResult:
        """禁用自动 HDR + 启动游戏 exe。"""
        if sys.platform != 'win32':
            return self._open_game_by_steam()

        hdr_op = DisableAutoHDR(self.ctx)
        hdr_op.execute()

        if self.ctx.game_account_config.game_path == '':
            return self.round_fail('未配置游戏路径，请前往 [ 账户管理 ] -> [ 游戏路径 ] 手动设置')
        full_path = self.ctx.game_account_config.game_path
        dir_path = os.path.dirname(full_path)
        exe_name = os.path.basename(full_path)
        log.info('尝试自动启动游戏 路径为 %s', full_path)
        command = f'cmd /c "start "" /d "{dir_path}" "{exe_name}"'
        if self.ctx.game_config.launch_argument:
            screen_size = self.ctx.game_config.screen_size
            screen_width = screen_size.split('x')[0]
            screen_height = screen_size.split('x')[1]
            full_screen = self.ctx.game_config.full_screen
            popup_window = "-popupwindow" if self.ctx.game_config.popup_window else ""
            monitor = self.ctx.game_config.monitor
            arguement = (f'{self.ctx.game_config.launch_argument_advance}'
                         f' -screen-width {screen_width} -screen-height {screen_height}'
                         f' -screen-fullscreen {full_screen} {popup_window} -monitor {monitor}')
            command = f'{command} {arguement}'
        command = f'{command} & exit"'
        log.info('命令行指令 %s', command)

        # CREATE_BREAKAWAY_FROM_JOB:启动器用进程组管理时,使子进程逃离 jobobject,
        # 避免 OneDragon-Launcher.exe 退出后游戏被杀死。
        subprocess.Popen(
            command,
            creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB
        )

        return self.round_success(wait=5)

    def _open_game_by_steam(self) -> OperationRoundResult:
        """Linux：经 Steam(Proton) 启动游戏，后续在等待节点里自动点掉 HYP 启动器。"""
        log.info('经 Steam 启动游戏 appid=%s', game_const.STEAM_APP_ID)
        subprocess.Popen(
            ['steam', f'steam://rungameid/{game_const.STEAM_APP_ID}'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.round_success(wait=5)

    def _linux_click_hyp_start(self) -> bool:
        """Linux：若 HoYoPlay 启动器窗口存在，OCR 找到开始按钮并点击。

        Returns:
            是否执行了点击
        """
        from one_dragon.base.controller import linux_compositor, uinput_input
        from one_dragon.base.controller.pc_screenshot.xcomposite_screencapper import (
            XCompositeScreencapper,
        )
        from one_dragon.base.controller.x11_game_window import X11GameWindow

        hyp_win = X11GameWindow()
        hyp_win.update_win_title(_HYP_WIN_TITLE)
        hyp_win.init_win()
        if not hyp_win.is_win_valid:
            return False
        rect = hyp_win.win_rect
        if rect is None:
            return False

        # HYP 内容固定 1280x768 且窗口过小时会被裁切（按钮在右下角看不到），先最大化
        if rect.width < 1280 or rect.height < 768:
            log.info('HYP 启动器窗口过小 (%dx%d)，先最大化', rect.width, rect.height)
            linux_compositor.maximize_window_by_title(_HYP_WIN_TITLE)
            time.sleep(1)
            rect = hyp_win.win_rect
            if rect is None or rect.width < 1280 or rect.height < 768:
                return False

        capper = XCompositeScreencapper(hyp_win, rect.width, rect.height)
        screen = capper.capture(rect, independent=True)
        if screen is None:
            return False

        ocr_result = self.ctx.ocr.run_ocr(screen)
        for text, mrl in ocr_result.items():
            if any(target in text for target in _HYP_START_TEXTS):
                hyp_win.active()
                match = mrl.max
                click_x = rect.x1 + match.center.x
                click_y = rect.y1 + match.center.y
                log.info('HYP 启动器点击 [%s] (%d, %d)', text, click_x, click_y)
                uinput_input.click_at(click_x, click_y)
                return True
        return False

    @node_from(from_name='打开游戏')
    @operation_node(name='等待游戏打开', node_max_retry_times=180, screenshot_before_round=False)
    def wait_game(self) -> OperationRoundResult:
        """等游戏窗口就绪 → 激活窗口 + 恢复 HDR。"""
        self.ctx.controller.init_game_win()
        if self.ctx.controller.is_game_window_ready:
            self.ctx.controller.active_window()
            hdr_op = EnableAutoHDR(self.ctx)
            hdr_op.execute()
            return self.round_success()

        if sys.platform != 'win32':
            # Steam 版会先弹 HoYoPlay 启动器，自动点掉开始按钮（best-effort，
            # 失败时用户可手动点「开始游戏」，本节点会一直等游戏本体窗口出现）
            clicked = False
            try:
                clicked = self._linux_click_hyp_start()
            except Exception:
                log.debug('HYP 启动器处理失败', exc_info=True)
            if not clicked:
                log.info('如长时间未进入游戏，可手动点击启动器的「开始游戏」')

        return self.round_retry(wait=1)
