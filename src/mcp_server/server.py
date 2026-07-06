#!/usr/bin/env python3
"""
MCP Server for DouK-Downloader.
Provides AI-callable tools for downloading Douyin/TikTok videos and querying content.

Transport: streamable-http (MCP over HTTP)

Environment variables:
    DOWNLOAD_DIR    - Fixed directory for downloaded files (required)
    DOUYIN_COOKIE   - Douyin cookie string (optional but recommended)
    TIKTOK_COOKIE   - TikTok cookie string (optional but recommended)
    PROXY           - HTTP/SOCKS proxy address (optional)
    MCP_HOST        - Host to bind (default: 127.0.0.1)
    MCP_PORT        - Port to bind (default: 8000)
"""

import json
import os
import sys
from pathlib import Path

# Ensure project root is in path for imports
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from mcp.server.fastmcp import FastMCP

from src.custom import PROJECT_ROOT
from src.config import Parameter, Settings
from src.application.main_terminal import TikTok
from src.module import Cookie
from src.record import BaseLogger, LoggerManager
from src.tools import ColorfulConsole
from src.manager import Database, DownloadRecorder

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", str(Path.home() / "Downloads" / "douk_videos"))
DOUYIN_COOKIE = os.environ.get("DOUYIN_COOKIE", "")
TIKTOK_COOKIE = os.environ.get("TIKTOK_COOKIE", "")
PROXY = os.environ.get("PROXY", "")
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

# Ensure download directory exists
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Global state – lazily initialised on first tool call
# ---------------------------------------------------------------------------
_parameter: Parameter | None = None
_tiktok: TikTok | None = None
_database: Database | None = None
_logger: BaseLogger | None = None


async def _ensure_initialised() -> None:
    """Initialise the TikTok downloader stack once, reusing existing state if available."""
    global _parameter, _tiktok, _database, _logger

    if _tiktok is not None:
        return

    console = ColorfulConsole(debug=False)

    settings = Settings(PROJECT_ROOT, console)
    settings_data = settings.read()

    # Override settings with environment variables
    if DOUYIN_COOKIE:
        settings_data["cookie"] = DOUYIN_COOKIE
    if TIKTOK_COOKIE:
        settings_data["cookie_tiktok"] = TIKTOK_COOKIE
    if PROXY:
        settings_data["proxy"] = PROXY
        settings_data["proxy_tiktok"] = PROXY

    # Force download directory and disable folder-mode nesting
    settings_data["root"] = DOWNLOAD_DIR
    settings_data["folder_name"] = ""
    settings_data["folder_mode"] = False
    settings_data["download"] = True

    cookie_obj = Cookie(settings, console)

    _database = Database()
    await _database.__aenter__()

    recorder = DownloadRecorder(
        _database,
        settings_data.get("Record", 0),
        console,
    )

    _parameter = Parameter(
        settings,
        cookie_obj,
        logger=BaseLogger,
        console=console,
        recorder=recorder,
        **settings_data,
    )
    _parameter.set_headers_cookie()

    _tiktok = TikTok(
        _parameter,
        _database,
        server_mode=True,
    )


async def _close() -> None:
    """Tear down resources held by the downloader stack."""
    global _parameter, _tiktok, _database, _logger
    if _parameter:
        try:
            await _parameter.close_client()
        except Exception:
            pass
    if _database:
        try:
            await _database.__aexit__(None, None, None)
        except Exception:
            pass
    _parameter = None
    _tiktok = None
    _database = None
    _logger = None


# ---------------------------------------------------------------------------
# Helper: extract post ID from a URL or raw string
# ---------------------------------------------------------------------------
async def _extract_ids(url: str, tiktok: bool = False) -> list[str]:
    """Parse a Douyin/TikTok URL (or raw ID) and return a list of post IDs."""
    await _ensure_initialised()
    link_obj = _tiktok.links_tiktok if tiktok else _tiktok.links
    raw = url.strip()
    # If the input looks like a pure numeric ID, return it directly
    if raw.isdigit():
        return [raw]
    ids = await link_obj.run(raw)
    if not ids or not any(ids):
        return []
    return list(ids)


def _format_result(success: bool, message: str, data: dict | list | None = None) -> str:
    """Return a JSON string suitable for MCP tool responses."""
    return json.dumps(
        {
            "success": success,
            "message": message,
            "data": data,
        },
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# FastMCP server & tool definitions
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "douk-downloader",
    instructions=(
        "DouK-Downloader MCP Server – Download Douyin/TikTok videos/images "
        "and query post/account/live/comment/search data. "
        "Set DOWNLOAD_DIR env to specify where files are saved."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
)


# ---- Download tools -------------------------------------------------------

@mcp.tool()
async def douyin_download(url: str) -> str:
    """
    下载单个抖音视频/图集作品到本地指定目录。
    Download a single Douyin video/image post to the local download directory.

    Args:
        url: 抖音作品链接或作品ID (e.g. "https://v.douyin.com/xxx" or numeric ID)
    """
    await _ensure_initialised()
    ids = await _extract_ids(url, tiktok=False)
    if not ids:
        return _format_result(False, f"无法从链接中提取作品 ID: {url}")

    root, params, logger_fn = _tiktok.record.run(_parameter)
    async with logger_fn(root, console=_parameter.console, **params) as record:
        from src.interface import Detail

        detail_data = []
        for detail_id in ids:
            data = await Detail(
                _parameter, cookie=None, proxy=None, detail_id=detail_id
            ).run()
            if data:
                detail_data.append(data)

        if not detail_data:
            return _format_result(False, f"获取作品数据失败，请检查 Cookie 是否有效: {ids}")

        extracted = await _tiktok.extractor.run(detail_data, record, tiktok=False)
        await _tiktok.downloader.run(extracted, "detail", tiktok=False)

        files = []
        for item in extracted:
            downloads = item.get("downloads")
            if downloads:
                if isinstance(downloads, str):
                    files.append(downloads)
                else:
                    files.extend(downloads)

        return _format_result(
            True,
            f"成功下载 {len(ids)} 个作品",
            {
                "ids": ids,
                "file_count": len(files),
                "files": files,
                "download_dir": DOWNLOAD_DIR,
            },
        )


@mcp.tool()
async def tiktok_download(url: str) -> str:
    """
    下载单个TikTok视频/图集作品到本地指定目录。
    Download a single TikTok video/image post to the local download directory.

    Args:
        url: TikTok作品链接或作品ID
    """
    await _ensure_initialised()
    ids = await _extract_ids(url, tiktok=True)
    if not ids:
        return _format_result(False, f"无法从链接中提取作品 ID: {url}")

    root, params, logger_fn = _tiktok.record.run(_parameter)
    async with logger_fn(root, console=_parameter.console, **params) as record:
        from src.interface import DetailTikTok

        detail_data = []
        for detail_id in ids:
            data = await DetailTikTok(
                _parameter, cookie=None, proxy=None, detail_id=detail_id
            ).run()
            if data:
                detail_data.append(data)

        if not detail_data:
            return _format_result(False, f"获取TikTok作品数据失败: {ids}")

        extracted = await _tiktok.extractor.run(detail_data, record, tiktok=True)
        await _tiktok.downloader.run(extracted, "detail", tiktok=True)

        files = []
        for item in extracted:
            if item.get("downloads"):
                files.extend(item["downloads"])

        return _format_result(
            True,
            f"成功下载 {len(ids)} 个TikTok作品",
            {
                "ids": ids,
                "file_count": len(files),
                "files": files,
                "download_dir": DOWNLOAD_DIR,
            },
        )


# ---- Query tools (data-only, no download) ---------------------------------

@mcp.tool()
async def douyin_detail(url: str) -> str:
    """
    获取抖音单个作品详细信息（不下载文件）。
    Get Douyin post detail info without downloading files.

    Args:
        url: 抖音作品链接或作品ID
    """
    await _ensure_initialised()
    ids = await _extract_ids(url, tiktok=False)
    if not ids:
        return _format_result(False, f"无法从链接中提取作品 ID: {url}")

    from src.interface import Detail

    details = []
    for detail_id in ids:
        data = await Detail(
            _parameter, cookie=None, proxy=None, detail_id=detail_id
        ).run()
        if data:
            details.append(data)

    if not details:
        return _format_result(False, f"获取作品数据失败，请检查 Cookie: {ids}")

    return _format_result(True, f"获取到 {len(details)} 个作品信息", {"posts": details})


@mcp.tool()
async def tiktok_detail(url: str) -> str:
    """
    获取TikTok单个作品详细信息（不下载文件）。
    Get TikTok post detail info without downloading files.

    Args:
        url: TikTok作品链接或作品ID
    """
    await _ensure_initialised()
    ids = await _extract_ids(url, tiktok=True)
    if not ids:
        return _format_result(False, f"无法从链接中提取作品 ID: {url}")

    from src.interface import DetailTikTok

    details = []
    for detail_id in ids:
        data = await DetailTikTok(
            _parameter, cookie=None, proxy=None, detail_id=detail_id
        ).run()
        if data:
            details.append(data)

    if not details:
        return _format_result(False, f"获取TikTok作品数据失败: {ids}")

    return _format_result(True, f"获取到 {len(details)} 个作品信息", {"posts": details})


@mcp.tool()
async def douyin_comment(detail_id: str, count: int = 20) -> str:
    """
    获取抖音作品评论数据。
    Get Douyin post comments.

    Args:
        detail_id: 抖音作品ID
        count: 获取评论数量（默认20）
    """
    await _ensure_initialised()
    data = await _tiktok.comment_handle_single(
        detail_id,
        cookie=None,
        proxy=None,
        source=False,
        pages=1,
        count=count,
    )
    if data:
        return _format_result(
            True, f"获取到 {len(data)} 条评论", {"comments": data}
        )
    return _format_result(False, f"获取评论数据失败: {detail_id}")


@mcp.tool()
async def douyin_live(web_rid: str) -> str:
    """
    获取抖音直播拉流地址。
    Get Douyin live stream URLs.

    Args:
        web_rid: 抖音直播 web_rid
    """
    await _ensure_initialised()
    data = await _tiktok.get_live_data(web_rid)
    if data:
        return _format_result(True, "获取直播数据成功", {"live": data})
    return _format_result(False, f"获取直播数据失败: {web_rid}")


@mcp.tool()
async def tiktok_live(room_id: str) -> str:
    """
    获取TikTok直播拉流地址。
    Get TikTok live stream URLs.

    Args:
        room_id: TikTok直播 room_id
    """
    await _ensure_initialised()
    data = await _tiktok.get_live_data_tiktok(room_id)
    if data:
        return _format_result(True, "获取TikTok直播数据成功", {"live": data})
    return _format_result(False, f"获取TikTok直播数据失败: {room_id}")


# ---- Account tools --------------------------------------------------------

@mcp.tool()
async def douyin_account(
    sec_user_id: str,
    count: int = 10,
    tab: str = "post",
) -> str:
    """
    获取抖音账号作品列表数据（不下载文件）。
    Get Douyin account posts list without downloading.

    Args:
        sec_user_id: 抖音账号 sec_uid
        count: 获取作品数量（默认10）
        tab: 页面类型 (post/favorite/collection)
    """
    await _ensure_initialised()
    data = await _tiktok.deal_account_detail(
        0,
        sec_user_id,
        tab=tab,
        api=True,
        source=False,
        cookie=None,
        proxy=None,
        count=count,
    )
    if data:
        count_found = len(data) if isinstance(data, list) else 1
        return _format_result(
            True, f"获取到 {count_found} 个作品", {"posts": data}
        )
    return _format_result(False, f"获取账号数据失败: {sec_user_id}")


@mcp.tool()
async def tiktok_account(
    sec_user_id: str,
    count: int = 10,
    tab: str = "post",
) -> str:
    """
    获取TikTok账号作品列表数据（不下载文件）。
    Get TikTok account posts list without downloading.

    Args:
        sec_user_id: TikTok账号 secUid
        count: 获取作品数量（默认10）
        tab: 页面类型 (post/favorite)
    """
    await _ensure_initialised()
    data = await _tiktok.deal_account_detail(
        0,
        sec_user_id,
        tab=tab,
        api=True,
        source=False,
        cookie=None,
        proxy=None,
        tiktok=True,
        count=count,
    )
    if data:
        count_found = len(data) if isinstance(data, list) else 1
        return _format_result(
            True, f"获取到 {count_found} 个作品", {"posts": data}
        )
    return _format_result(False, f"获取TikTok账号数据失败: {sec_user_id}")


# ---- Search tools ---------------------------------------------------------

@mcp.tool()
async def douyin_search(
    keyword: str,
    count: int = 10,
    offset: int = 0,
    sort_type: int = 0,
    publish_time: int = 0,
) -> str:
    """
    搜索抖音内容（综合搜索）。
    Search Douyin content.

    Args:
        keyword: 搜索关键词
        count: 返回数量（默认10）
        offset: 起始页码
        sort_type: 排序方式 (0=综合, 1=最多点赞, 2=最新发布)
        publish_time: 发布时间过滤 (0=不限, 1=一天内, 7=一周内, 180=半年内)
    """
    await _ensure_initialised()
    from src.interface import Search

    data = await Search(
        _parameter,
        cookie=None,
        proxy=None,
        keyword=keyword,
        channel=0,  # general search
        offset=offset,
        count=count,
        pages=1,
        sort_type=sort_type,
        publish_time=publish_time,
    ).run(single_page=True)
    if data:
        count_found = len(data) if isinstance(data, list) else 1
        return _format_result(
            True, f"搜索到 {count_found} 条结果", {"results": data}
        )
    return _format_result(False, f"搜索失败: {keyword}")


# ---- Main entry point -----------------------------------------------------

def main() -> None:
    """Entry point for the MCP server. Transport via MCP_TRANSPORT env (default: streamable-http)."""
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()