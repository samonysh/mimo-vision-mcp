# MiMo Vision MCP Server

一个支持**图像输入**的小米 MiMo 视觉语言模型（Vision LLM）的 MCP 服务器。它通过标准 MCP 协议对外暴露工具，内部调用小米 MiMo 的 OpenAI 兼容接口 `POST /v1/chat/completions`，完成多模态（图像 + 文本）对话。

## 功能特性

- 支持图像 **URL** 与**本地文件路径**两种输入方式（本地图片自动编码为 data URL）。
- 内置 3 个工具：图像描述、多模态问答、OCR 文字识别。
- 通过环境变量灵活配置 API Key、Base URL、默认模型与最大 token 数。
- 使用 `FastMCP` / 官方 `mcp` SDK 构建，兼容任意 MCP 客户端（Claude Desktop、Cursor、TRAE 等）。
- 支持 **stdio** 与 **streamable-http** 两种传输模式，可本地运行也可 Docker 远程部署。
- 提供 Dockerfile 与 docker-compose，内置 GitHub Actions 自动构建多架构镜像（amd64/arm64）。

## 目录结构

```
mimo-vision-mcp/
├── server.py               # MCP 服务器核心代码
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板
├── Dockerfile              # Docker 构建文件
├── docker-compose.yml      # Docker Compose 编排文件
├── .dockerignore
├── .gitignore
├── .github/
│   └── workflows/
│       └── docker-publish.yml  # GitHub Actions 自动构建镜像
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
cd mimo-vision-mcp
pip install -r requirements.txt
```

### 2. 配置 API Key

复制 `.env.example` 为 `.env` 并填入你的小米 MiMo API Key（也可直接通过环境变量注入）：

```bash
copy .env.example .env   # Windows
# 编辑 .env，设置 MIMO_API_KEY=你的Key
```

| 环境变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `MIMO_API_KEY` | 是 | - | 小米 MiMo API Key |
| `MIMO_BASE_URL` | 否 | `https://api.xiaomimimo.com/v1` | API 根地址 |
| `MIMO_MODEL` | 否 | `mimo-v2.5` | 默认模型 |
| `MIMO_MAX_TOKENS` | 否 | `1024` | 默认最大生成 token 数 |
| `MCP_TRANSPORT` | 否 | `stdio` | 传输模式：`stdio` 或 `streamable-http` |
| `MCP_HOST` | 否 | `0.0.0.0` | HTTP 模式监听地址 |
| `MCP_PORT` | 否 | `8000` | HTTP 模式监听端口 |

### 3. 启动服务器

```bash
python server.py
```

默认通过标准输入输出（stdio）模式与 MCP 客户端通信，供客户端配置为本地 MCP 服务器运行。

## 配置到 MCP 客户端

在你的 MCP 客户端配置中注册该服务器，例如：

```json
{
  "mcpServers": {
    "mimo-vision": {
      "command": "python",
      "args": ["C:/path/to/mimo-vision-mcp/server.py"],
      "env": {
        "MIMO_API_KEY": "你的Key"
      }
    }
  }
}
```

## 提供的工具

### 1. `describe_image` 描述图片

- `image`: 图片 URL 或本地绝对路径（必填）
- `prompt`: 可选，自定义描述指令，默认“请描述这张图片的内容”
- `model` / `max_tokens`: 可选，覆盖默认值

### 2. `chat_with_image` 图片问答

- `image`: 图片 URL 或本地绝对路径（必填）
- `question`: 针对图片的问题（必填）
- `system_prompt`: 可选，自定义 system 提示词
- `model` / `max_tokens`: 可选，覆盖默认值

### 3. `ocr_image` 文字识别

- `image`: 图片 URL 或本地绝对路径（必填）
- `model` / `max_tokens`: 可选，覆盖默认值

## 调用示例

```
描述这张图片  →  describe_image(image="https://example-files.cnbj1.mi-fds.com/example-files/image/image_example.png")
识别图中表格  →  ocr_image(image="C:/data/table.png")
提问图片内容  →  chat_with_image(image="https://.../photo.jpg", question="图中有几个人？")
```

## 实现说明

- 请求报文严格遵循你提供的接口格式：使用 `api-key` 请求头、`max_completion_tokens` 字段、`image_url` 多模态内容结构。
- 本地图片通过 `data:{mime};base64,{...}` 编码后作为 `image_url.url` 传入，兼容 OpenAI 视觉接口规范。

---

## Docker 部署

本项目支持通过 Docker 快速部署为远程 MCP 服务器。推送到 GitHub 后，GitHub Actions 会自动构建多架构镜像（amd64/arm64）并发布到 GHCR。

### 方式一：使用预构建镜像（推荐）

#### 1. 创建 `.env` 文件

在你的 VPS 上创建一个目录，放入 `.env` 文件：

```bash
MIMO_API_KEY=你的MiMo_API_Key
# 以下为可选项，按需修改
# MIMO_BASE_URL=https://api.xiaomimimo.com/v1
# MIMO_MODEL=mimo-v2.5
# MIMO_MAX_TOKENS=1024
```

#### 2. 创建 `docker-compose.yml`

```yaml
services:
  mimo-vision-mcp:
    image: ghcr.io/<你的GitHub用户名>/mimo-vision-mcp:latest
    ports:
      - "8000:8000"
    environment:
      - MIMO_API_KEY=${MIMO_API_KEY}
      - MIMO_BASE_URL=${MIMO_BASE_URL:-https://api.xiaomimimo.com/v1}
      - MIMO_MODEL=${MIMO_MODEL:-mimo-v2.5}
      - MIMO_MAX_TOKENS=${MIMO_MAX_TOKENS:-1024}
      - MCP_TRANSPORT=streamable-http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8000
    restart: unless-stopped
```

> 将 `<你的GitHub用户名>` 替换为实际的 GitHub 用户名。

#### 3. 启动服务

```bash
docker compose up -d
```

服务将在 `http://<你的VPS_IP>:8000` 上运行，MCP 端点为 `http://<你的VPS_IP>:8000/mcp`。

#### 4. 查看日志

```bash
docker compose logs -f
```

### 方式二：本地构建

```bash
docker compose up -d --build
```

### 连接远程 MCP 服务器

在支持远程 MCP 的客户端中配置：

```json
{
  "mcpServers": {
    "mimo-vision": {
      "url": "http://<你的VPS_IP>:8000/mcp"
    }
  }
}
```

### CI/CD 自动构建

推送到 `main` 分支或打 `v*` 标签时，GitHub Actions 会自动：

1. 构建 `linux/amd64` + `linux/arm64` 双架构镜像
2. 推送到 `ghcr.io/<你的GitHub用户名>/mimo-vision-mcp`
3. 自动打 `latest`、版本号等标签

镜像地址：`ghcr.io/<你的GitHub用户名>/mimo-vision-mcp:latest`