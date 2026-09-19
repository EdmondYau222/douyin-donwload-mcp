# DouK-Downloader MCP 服务

基于 [FastMCP](https://gofastmcp.com) 的全新 MCP（Model Context Protocol）服务，
将 DouK-Downloader 的抖音数据采集与下载能力封装为标准 MCP 工具，
供 Claude、Cursor、Cherry Studio 等支持 MCP 的客户端远程调用。

> 本文件是独立的 MCP 服务，不属于上游项目代码；
> 复用项目 `src/` 内部模块，**更新项目代码后如遇导入报错请同步检查本服务**。

## 启动

```bash
# 使用项目对应的 Python 环境（当前为 E:\3rd\miniconda_env\tiktokdownloader）
python mcp_server.py
```

服务以 **Streamable HTTP** 方式运行在：

```
http://127.0.0.1:58081/mcp
```

依赖：`fastmcp`（已随本服务安装；重装环境时执行 `pip install fastmcp`）。

## Docker 部署（推荐）

Docker 相关文件集中在 `docker/` 目录（`Dockerfile.mcp`、`docker-compose.yml`
及其忽略规则）：

```bash
docker compose -f docker/docker-compose.yml up -d --build   # 构建镜像并启动
docker logs -f douk-mcp                                     # 查看日志
docker compose -f docker/docker-compose.yml down            # 停止
```

- 服务地址不变：`http://127.0.0.1:58081/mcp`（端口映射 `58081:58081`）
- `Volume/` 目录挂载进容器，Cookie、配置、下载数据库、下载文件全部持久化在宿主机
- 容器内已设置 `MCP_HOST=0.0.0.0`；如需修改端口，调整 `docker-compose.yml` 的端口映射即可
- 镜像基于 `python:3.12-slim-bookworm`，包含 Node.js（与上游官方镜像一致）

镜像导出/导入（用于分发到其他机器，导出文件在 `docker/` 目录）：

```bash
docker save douk-mcp:latest | gzip > docker/douk-mcp.tar.gz   # 导出（约 124MB）
docker load -i docker/douk-mcp.tar.gz                         # 导入后即可 docker run / compose up
```

注意：容器与本机直接运行的服务共用 `Volume/`（含 SQLite 数据库），
**两者不要同时运行**。

## 客户端接入示例

以支持 MCP HTTP 服务的客户端为例，添加如下配置：

```json
{
  "mcpServers": {
    "douk-downloader": {
      "url": "http://127.0.0.1:58081/mcp"
    }
  }
}
```

## 工具列表（12 个，均针对抖音平台）

| 工具 | 功能 | 下载文件 |
| --- | --- | --- |
| `extract_work_ids` | 从分享文本/链接提取作品 ID（纯本地解析） | 否 |
| `get_work_data` | 获取单个作品数据（含下载地址） | 否 |
| `download_works` | 批量下载链接作品 | ✅ |
| `get_account_works` | 获取账号作品列表（自动翻页） | 否 |
| `download_account_works` | 批量下载账号作品 | ✅ |
| `get_mix_works` | 获取合集作品列表 | 否 |
| `download_mix_works` | 批量下载合集作品 | ✅ |
| `get_account_info` | 获取账号详细数据（昵称、粉丝数等） | 否 |
| `get_comments` | 采集作品评论数据 | 否 |
| `search_works` | 采集综合搜索结果 | 否 |
| `get_hotlist` | 采集抖音热榜 | 否 |
| `get_live_url` | 获取直播间拉流地址（flv/m3u8） | 否 |

通用输入约定：大多数 `work` / `user` / `mix` 参数**直接粘贴分享链接或文本**即可，
服务会自动提取 ID；也可以直接传 19 位作品 ID、`sec_user_id`、合集 ID。

## 行为说明

- **配置来源**：Cookie、下载路径等全部读取 `Volume/settings.json`，修改后需重启服务；
- **下载目录**：`root` 未设置时默认为 `Volume/`，命名规则由 `name_format` 等参数控制；
- **下载去重**：已下载过的作品会自动跳过（基于作品下载记录数据库）；
- **串行执行**：所有工具调用内部互斥，避免并发下载冲突；
- **compact 返回**：列表类工具默认只返回关键字段（`compact=True`），
  需要全量字段时传 `compact=False`，接口原始数据传 `source=True`；
- **耗时提示**：账号/合集作品会自动翻页抓取全部数据，大账号可能耗时数分钟，
  客户端请设置足够长的请求超时（建议 ≥ 300 秒）。

## 架构

```
MCP 客户端 ──HTTP──> FastMCP (mcp_server.py)
                          │
                          ├── TikTok (src/application/main_terminal.py)  ← 与 Web API 模式同款编排层
                          │      ├── Extractor   数据提取
                          │      ├── Downloader  文件下载
                          │      └── RecordManager 下载记录
                          └── Parameter  Cookie / 签名参数 / 配置
```

初始化流程与主程序 `main.py` 的 `run()` 一致：加载数据库与配置 →
构建 `Parameter`（含 msToken/ttwid 更新线程）→ 构建 `TikTok` 编排实例 →
注册工具并启动 HTTP 服务，全部运行在同一个事件循环。

## 注意事项

1. 本服务仅封装**抖音**平台；TikTok 需要额外配置 Cookie 与代理，暂未提供。
2. 服务与主程序共用同一套配置和下载记录，请避免同时运行终端模式的下载任务。
3. 接口能否正常访问取决于抖音风控状态与项目签名模块；若工具普遍报错，
   先运行 `python main.py` 确认主程序功能是否正常。
