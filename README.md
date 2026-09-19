# TikTokDownloader MCP 增强版

> 🤖 **本项目由 Vibe Coding 方式开发** —— 基于开源项目 DouK-Downloader，
> 通过 AI Agent 结对完成代码升级、MCP 服务重构与 Docker 化部署，
> 人类负责提出需求与验收，AI 负责阅读源码、编写实现与测试验证。

基于 [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)
（DouK-Downloader）V5.8 Stable 构建的抖音 / TikTok 数据采集与下载工具，
在上游基础上**全新实现了 MCP 服务并完成 Docker 化**，
可一键部署后供 Claude、Cursor、Cherry Studio 等任意 MCP 客户端远程调用。

## ✨ 本仓库的增强内容

相对上游 master 分支，本仓库包含以下改动（均为 Vibe Coding 产出）：

| 改动 | 说明 |
| --- | --- |
| 🆕 全新 MCP 服务 | [`mcp_server.py`](mcp_server.py)：12 个抖音工具，Streamable HTTP 接入，详见 [README_MCP.md](README_MCP.md) |
| 🐳 Docker 一键部署 | [`docker/`](docker/) 目录：镜像构建 + 编排 + 镜像导出，开箱即用 |
| 🐛 修复跨平台 Bug | `settings.json` 读取兼容 UTF-8 BOM（Windows 写出的配置在 Linux 容器内解析失败的问题） |
| ⬆️ 升级到 V5.8 Stable | 同步上游 2026-09 最新代码，适配抖音新版签名风控（修复作品接口 403） |

> 上游官方 README 见 [README_UPSTREAM.md](README_UPSTREAM.md)（英文版 [README_EN.md](README_EN.md)）。

## 🧰 功能一览

**主程序**（终端交互 / Web API / Web UI，同上游）：
批量下载账号作品、链接作品、合集作品、收藏作品，采集评论、账号数据、搜索结果、热榜，
获取直播拉流地址等。

**MCP 服务**（本仓库新增，仅抖音平台）：

| 工具 | 功能 | 工具 | 功能 |
| --- | --- | --- | --- |
| `extract_work_ids` | 提取作品 ID | `get_account_info` | 账号详细数据 |
| `get_work_data` | 单作品数据 | `get_comments` | 评论数据 |
| `download_works` | 批量下载链接作品 | `search_works` | 综合搜索 |
| `get_account_works` | 账号作品列表 | `get_hotlist` | 热榜 |
| `download_account_works` | 下载账号作品 | `get_live_url` | 直播拉流地址 |
| `get_mix_works` / `download_mix_works` | 合集数据 / 下载 | | |

支持直接粘贴分享链接/文本，自动提取 ID；列表类工具默认返回精简字段，适合 LLM 阅读。

## 🚀 快速开始

### 方式一：Docker（推荐）

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

启动后 MCP 服务地址：`http://127.0.0.1:58081/mcp`，配置与下载数据持久化在 `Volume/` 目录。

### 方式二：直接运行

```bash
# 环境：Python 3.12，安装依赖
pip install -r requirements.txt fastmcp

# 配置 Cookie：先运行主程序按菜单提示写入
python main.py

# 启动 MCP 服务
python mcp_server.py
```

### MCP 客户端接入

```json
{
  "mcpServers": {
    "douk-downloader": {
      "url": "http://127.0.0.1:58081/mcp"
    }
  }
}
```

使用前需在 `Volume/settings.json` 中配置有效的抖音 Cookie（用主程序菜单 1/2 写入）。

## 📁 目录说明

```
├── main.py               # 主程序入口（上游原样）
├── mcp_server.py         # ★ 全新 MCP 服务（Vibe Coding 实现）
├── docker/               # ★ Docker 部署（Dockerfile / compose / 镜像导出）
├── Volume/               # 配置、Cookie、下载记录数据库、下载文件（运行时生成）
├── src/                  # 项目源码（上游 V5.8 + settings BOM 修复）
└── README_MCP.md         # ★ MCP 服务详细文档
```

## ⚠️ 使用须知

- 请遵守目标平台的服务条款与当地法律法规，仅供学习研究，勿用于商业用途
- 抖音风控持续变化，若接口异常请先运行 `python main.py` 确认主程序功能状态
- Cookie 失效后需重新获取；容器与本机服务共用 `Volume/`，**不要同时运行**

## 📄 许可与致谢

- 上游项目：[JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)，
  遵循 [GNU GPL v3.0](license) 开源，本仓库同样以 GPL v3.0 开源
- 感谢上游作者及签名模块参考项目
  [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)
- 本仓库的 MCP 服务、Docker 部署及文档由 AI 辅助（Vibe Coding）完成
