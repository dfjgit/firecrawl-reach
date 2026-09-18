# -*- coding: utf-8 -*-
"""适配器协议：每个引擎一个薄封装，只有一个 fetch()。"""

from abc import ABC, abstractmethod

from acquisition.sources import Source


class AdapterError(RuntimeError):
    """适配器抓取失败（信息可展示给用户，路由器记 error 不崩溃）。"""


class LoginRequiredError(AdapterError):
    """登录态缺失（从未登录过）：提示用户走官方命令登录，路由器记 blocked。"""


class LoginInvalidError(AdapterError):
    """抓取中识别到登录态失效（产物为空且日志含登录/验证特征），路由器记 blocked。"""


class Adapter(ABC):
    """适配器协议：fetch(source) -> list[Item]。"""

    @abstractmethod
    def fetch(self, source: Source) -> list:
        """抓取一个源，返回 Item 列表；失败抛 AdapterError。"""
        raise NotImplementedError
