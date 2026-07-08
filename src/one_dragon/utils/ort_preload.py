import ctypes
import importlib.util
import sys
from pathlib import Path

_loaded: bool = False


def preload_cuda_libs() -> None:
    """
    Linux 上预载 pip 版 NVIDIA CUDA 库（RTLD_GLOBAL），使 onnxruntime-gpu 能解析 libcudart 等依赖。
    必须在 import onnxruntime 之前执行；非 Linux、重复调用或未安装 nvidia 包时为 no-op。
    见 docs/develop/one_dragon/linux_port_design.md
    """
    global _loaded
    if _loaded or sys.platform != 'linux':
        return
    _loaded = True

    spec = importlib.util.find_spec('nvidia')
    if spec is None or not spec.submodule_search_locations:
        return

    so_list: list[Path] = []
    for loc in spec.submodule_search_locations:
        so_list.extend(Path(loc).glob('**/lib/*.so*'))

    # 两轮加载：第一轮可能因依赖顺序失败，第二轮补齐
    for _ in range(2):
        for so_path in sorted(so_list):
            try:
                ctypes.CDLL(str(so_path), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


# import 即生效，保证在任何 import onnxruntime 之前完成预载
preload_cuda_libs()
