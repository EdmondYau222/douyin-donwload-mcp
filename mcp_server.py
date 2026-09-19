"""
DouK-Downloader MCP 服务（全新实现）

基于 FastMCP 通过 Streamable HTTP 对外提供抖音数据采集与下载工具。

启动：
    python mcp_server.py

服务地址：
    http://127.0.0.1:58081/mcp

说明：
    - 复用项目 Web API 模式（APIServer/TikTok）的非交互调用链路，
      Cookie 与全部配置均读取 Volume/settings.json；
    - 所有工具串行执行（内部互斥锁），与项目终端模式的行为保持一致；
    - 仅封装抖音平台功能；TikTok 未配置 Cookie 与代理，暂不提供。
"""

from asyncio import Lock, run
from contextlib import asynccontextmanager
from json import dumps, loads
from os import environ
from re import compile as re_compile
from time import sleep
from typing import Optional

from fastmcp import FastMCP

from src.application import TikTokDownloader
from src.application.main_terminal import TikTok
from src.interface.template import API
from src.models import GeneralSearch

__all__ = ["DouKMCPServer", "main"]

SEC_UID_PATTERN = re_compile(r"^[A-Za-z0-9_-]{20,}$")


class DouKMCPServer:
    """DouK-Downloader MCP 服务：负责初始化下载器上下文并注册 MCP 工具。"""

    # 容器部署时设置环境变量 MCP_HOST=0.0.0.0 即可对外提供服务
    HOST = environ.get("MCP_HOST", "127.0.0.1")
    PORT = int(environ.get("MCP_PORT", "58081"))
    PARAMS_TIMEOUT = 60  # 等待 msToken 更新的最长秒数

    def __init__(self):
        self.app = None
        self.tt: TikTok | None = None
        self.lock = Lock()
        self.mcp = FastMCP(
            "DouK-Downloader",
            instructions=(
                "DouK-Downloader 抖音数据采集与下载服务。"
                "支持作品数据获取/下载、账号与合集作品、评论、搜索、热榜、直播地址。"
                "配置文件：Volume/settings.json（修改后需重启服务）。"
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    async def bootstrap(self):
        """初始化下载器上下文（与主程序 run() 流程一致，无交互）。"""
        self.app = TikTokDownloader()
        await self.app.database.__aenter__()
        await self.app.read_config()
        self.app.check_config()
        await self.app.check_settings(False)
        self.tt = TikTok(
            self.app.parameter,
            self.app.database,
            server_mode=True,
        )
        self._wait_params_ready()

    def _wait_params_ready(self):
        """等待后台线程完成 msToken/ttwid 更新。"""
        waited = 0
        while not API.params.get("msToken"):
            if waited >= self.PARAMS_TIMEOUT:
                self.app.console.warning("抖音参数更新超时，部分功能可能不可用！")
                return
            sleep(1)
            waited += 1
        self.app.console.info("抖音参数就绪，MCP 服务开始对外提供服务。")

    async def shutdown(self):
        if self.app:
            await self.app.parameter.close_client()
            await self.app.database.__aexit__(None, None, None)
            self.app.close()

    # ------------------------------------------------------------------
    # 内部工具函数
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _record(self):
        root, params, logger = self.tt.record.run(self.tt.parameter)
        async with logger(root, console=self.tt.console, **params) as record:
            yield record

    @staticmethod
    def _json(data):
        """将返回数据转换为可 JSON 序列化的安全结构。"""
        return loads(dumps(data, ensure_ascii=False, default=str))

    COMPACT_KEYS = (
        "id",
        "desc",
        "type",
        "create_time",
        "nickname",
        "sec_uid",
        "duration",
        "digg_count",
        "comment_count",
        "collect_count",
        "share_count",
        "downloads",
        "static_cover",
        "share_url",
    )

    @classmethod
    def _compact(cls, data: list) -> list:
        """压缩作品数据列表，仅保留对 LLM 客户端有意义的字段。"""
        return [
            {k: v for k, v in item.items() if k in cls.COMPACT_KEYS}
            if isinstance(item, dict)
            else item
            for item in data
        ]

    async def _resolve_work_ids(self, work: str) -> list[str]:
        """支持 19 位作品 ID 或任意包含链接的分享文本。"""
        if work.strip().isdigit():
            return [work.strip()]
        return await self.tt.links.run(work)

    async def _resolve_sec_user_id(self, user: str) -> str:
        """支持账号主页链接或 sec_user_id。"""
        ids = await self.tt.links.run(user, type_="user")
        if ids:
            return ids[0]
        if SEC_UID_PATTERN.fullmatch(user.strip()):
            return user.strip()
        return ""

    async def _resolve_mix(self, mix: str) -> tuple:
        """支持合集链接、合集内作品链接或合集 ID，返回 (is_mix, id)。"""
        is_mix, ids = await self.tt.links.run(mix, type_="mix")
        if ids:
            return is_mix, ids[0]
        if mix.strip().isdigit():
            return True, mix.strip()
        return None, ""

    # ------------------------------------------------------------------
    # MCP 工具注册
    # ------------------------------------------------------------------
    def _register_tools(self):
        mcp = self.mcp

        # ------------------------------------------------------------------
        @mcp.tool
        async def extract_work_ids(text: str) -> dict:
            """从分享文本或链接中提取抖音作品 ID（不请求数据，仅本地解析）。"""
            async with self.lock:
                ids = await self.tt.links.run(text)
                return self._json({"count": len(ids), "ids": ids})

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_work_data(work: str, source: bool = False) -> dict:
            """获取单个作品的数据（不下载文件）。

            Args:
                work: 作品 ID（19 位数字）或包含作品链接的分享文本
                source: True 返回接口原始数据；False 返回整理后的数据（含下载地址）
            """
            async with self.lock:
                ids = await self._resolve_work_ids(work)
                if not ids:
                    return {"success": False, "message": "无法从输入中解析作品 ID"}
                async with self._record() as record:
                    data = await self.tt._handle_detail(
                        ids,
                        False,
                        record,
                        True,
                        source,
                        None,
                        None,
                    )
                if not data:
                    return {"success": False, "message": "获取作品数据失败"}
                return self._json({"success": True, "data": data[0]})

        # ------------------------------------------------------------------
        @mcp.tool
        async def download_works(links_text: str) -> dict:
            """批量下载作品（视频/图集），文件保存至配置的下载目录。

            Args:
                links_text: 一个或多个作品链接/分享文本（可混合多行）
            """
            async with self.lock:
                ids = await self._resolve_work_ids(links_text)
                if not ids:
                    return {"success": False, "message": "无法从输入中解析作品 ID"}
                async with self._record() as record:
                    preview = await self.tt._handle_detail(
                        ids,
                        False,
                        record,
                    )
                return self._json(
                    {
                        "success": bool(ids),
                        "message": "下载任务已执行，文件保存至下载目录",
                        "total": len(ids),
                        "ids": ids,
                        "preview": preview or "",
                        "download_dir": str(self.tt.parameter.root),
                    }
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_account_works(
            user: str,
            tab: str = "post",
            earliest: str = "",
            latest: str = "",
            pages: Optional[int] = None,
            source: bool = False,
            compact: bool = True,
        ) -> dict:
            """获取账号作品数据列表（不下载文件）。

            注意：发布页会自动翻页获取全部作品，大账号可能耗时数分钟。

            Args:
                user: 账号主页链接或 sec_user_id
                tab: 页面类型：post(发布)/favorite(喜欢)/collection(收藏)
                earliest: 最早发布日期，格式 YYYY-MM-DD
                latest: 最晚发布日期，格式 YYYY-MM-DD
                pages: 最大翻页次数（仅 favorite 页有效）
                source: True 返回接口原始数据；False 返回整理后的数据
                compact: True 仅返回关键字段，适合 LLM 阅读；False 返回全量字段
            """
            async with self.lock:
                sec_user_id = await self._resolve_sec_user_id(user)
                if not sec_user_id:
                    return {"success": False, "message": "无法解析账号 sec_user_id"}
                data = await self.tt.deal_account_detail(
                    0,
                    sec_user_id,
                    tab=tab,
                    earliest=earliest,
                    latest=latest,
                    pages=pages,
                    api=True,
                    source=source,
                )
                if not data:
                    return {"success": False, "message": "获取账号作品数据失败"}
                return self._json(
                    {
                        "success": True,
                        "count": len(data),
                        "data": self._compact(data) if compact else data,
                    }
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def download_account_works(
            user: str,
            tab: str = "post",
            earliest: str = "",
            latest: str = "",
            pages: Optional[int] = None,
        ) -> dict:
            """批量下载账号作品，文件保存至配置的下载目录。

            Args:
                user: 账号主页链接或 sec_user_id
                tab: 页面类型：post(发布)/favorite(喜欢)/collection(收藏)
                earliest: 最早发布日期，格式 YYYY-MM-DD
                latest: 最晚发布日期，格式 YYYY-MM-DD
                pages: 最大翻页次数（仅 favorite 页有效）
            """
            async with self.lock:
                sec_user_id = await self._resolve_sec_user_id(user)
                if not sec_user_id:
                    return {"success": False, "message": "无法解析账号 sec_user_id"}
                result = await self.tt.deal_account_detail(
                    0,
                    sec_user_id,
                    tab=tab,
                    earliest=earliest,
                    latest=latest,
                    pages=pages,
                )
                return self._json(
                    {
                        "success": bool(result),
                        "message": (
                            "下载任务已执行，文件保存至下载目录"
                            if result
                            else "下载账号作品失败"
                        ),
                        "sec_user_id": sec_user_id,
                        "download_dir": str(self.tt.parameter.root),
                    }
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_mix_works(
            mix: str,
            count: Optional[int] = None,
            cursor: Optional[int] = None,
            source: bool = False,
            compact: bool = True,
        ) -> dict:
            """获取合集作品数据列表（不下载文件）。

            Args:
                mix: 合集链接、合集内作品链接或合集 ID
                count: 每次请求数量
                cursor: 翻页游标
                source: True 返回接口原始数据；False 返回整理后的数据
                compact: True 仅返回关键字段，适合 LLM 阅读；False 返回全量字段
            """
            async with self.lock:
                is_mix, id_ = await self._resolve_mix(mix)
                if not id_:
                    return {"success": False, "message": "无法解析合集 ID"}
                data = await self.tt.deal_mix_detail(
                    is_mix,
                    id_,
                    api=True,
                    source=source,
                    cursor=cursor or 0,
                    count=count or 12,
                )
                if not data:
                    return {"success": False, "message": "获取合集作品数据失败"}
                return self._json(
                    {
                        "success": True,
                        "count": len(data),
                        "data": self._compact(data) if compact else data,
                    }
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def download_mix_works(mix: str) -> dict:
            """批量下载合集作品，文件保存至配置的下载目录。

            Args:
                mix: 合集链接、合集内作品链接或合集 ID
            """
            async with self.lock:
                is_mix, id_ = await self._resolve_mix(mix)
                if not id_:
                    return {"success": False, "message": "无法解析合集 ID"}
                result = await self.tt.deal_mix_detail(is_mix, id_)
                return self._json(
                    {
                        "success": bool(result),
                        "message": (
                            "下载任务已执行，文件保存至下载目录"
                            if result
                            else "下载合集作品失败"
                        ),
                        "id": id_,
                        "download_dir": str(self.tt.parameter.root),
                    }
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_account_info(user: str) -> dict:
            """获取账号详细数据（昵称、签名、粉丝数等）。

            Args:
                user: 账号主页链接或 sec_user_id
            """
            async with self.lock:
                sec_user_id = await self._resolve_sec_user_id(user)
                if not sec_user_id:
                    return {"success": False, "message": "无法解析账号 sec_user_id"}
                data = await self.tt.get_user_info_data(
                    False,
                    sec_user_id=sec_user_id,
                )
                if not data:
                    return {"success": False, "message": "获取账号信息失败"}
                return self._json({"success": True, "data": data})

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_comments(
            work: str,
            pages: int = 1,
            count: int = 20,
            reply: bool = False,
        ) -> dict:
            """采集作品评论数据。

            Args:
                work: 作品 ID（19 位数字）或包含作品链接的分享文本
                pages: 最大翻页次数
                count: 每次请求数量
                reply: 是否同时采集评论回复
            """
            async with self.lock:
                ids = await self._resolve_work_ids(work)
                if not ids:
                    return {"success": False, "message": "无法从输入中解析作品 ID"}
                data = await self.tt.comment_handle_single(
                    ids[0],
                    source=True,
                    pages=pages,
                    count=count,
                    reply=reply,
                )
                if not data:
                    return {"success": False, "message": "获取评论数据失败"}
                return self._json(
                    {"success": True, "count": len(data), "data": data}
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def search_works(
            keyword: str,
            count: int = 10,
            pages: int = 1,
            sort_type: int = 0,
            publish_time: int = 0,
            duration: int = 0,
        ) -> dict:
            """采集搜索结果数据（综合搜索）。

            Args:
                keyword: 搜索关键词
                count: 每次请求数量（最小 5）
                pages: 总页数
                sort_type: 排序：0(综合)/1(最新)/2(最热)
                publish_time: 发布时间：0(不限)/1(一天内)/7(一周内)/180(半年内)
                duration: 时长：0(不限)/1(一分钟内)/2(一到五分钟)/3(五分钟以上)
            """
            async with self.lock:
                model = GeneralSearch(
                    keyword=keyword,
                    count=count,
                    pages=pages,
                    sort_type=sort_type,
                    publish_time=publish_time,
                    duration=duration,
                )
                data = await self.tt.deal_search_data(model, source=True)
                if not data:
                    return {"success": False, "message": "搜索失败"}
                if not any(data):
                    return {"success": True, "count": 0, "data": []}
                return self._json(
                    {"success": True, "count": len(data), "data": data}
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_hotlist() -> dict:
            """采集抖音热榜数据。"""
            async with self.lock:
                time_, board = await self.tt._deal_hot_data(source=True)
                if not board:
                    return {"success": False, "message": "获取热榜数据失败"}
                return self._json(
                    {"success": True, "time": time_, "board": board}
                )

        # ------------------------------------------------------------------
        @mcp.tool
        async def get_live_url(url: str) -> dict:
            """获取直播间的拉流地址（flv/m3u8）。

            Args:
                url: 直播间链接，如 https://live.douyin.com/xxxxx
            """
            async with self.lock:
                web_rids = await self.tt.links.run(url, type_="live")
                if not web_rids:
                    return {"success": False, "message": "无法解析直播间链接"}
                raw = [await self.tt.get_live_data(i) for i in web_rids]
                stream = await self.tt.extractor.run(raw, None, "live")
                if not any(stream):
                    return {"success": False, "message": "获取直播数据失败"}
                return self._json({"success": True, "data": stream})

    # ------------------------------------------------------------------
    # 启动入口
    # ------------------------------------------------------------------
    async def serve(self):
        await self.bootstrap()
        try:
            await self.mcp.run_async(
                transport="http",
                host=self.HOST,
                port=self.PORT,
                path="/mcp",
            )
        finally:
            await self.shutdown()


def main():
    server = DouKMCPServer()
    print(
        f"DouK-Downloader MCP 服务启动中: "
        f"http://{server.HOST}:{server.PORT}/mcp"
    )
    try:
        run(server.serve())
    except KeyboardInterrupt:
        print("MCP 服务已停止")


if __name__ == "__main__":
    main()
