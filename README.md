# MiMo Vision MCP Server

一个支持**图像输入**的小米 MiMo 视觉语言模型（Vision LLM）的 MCP 服务器。它通过标准 MCP 协议对外暴露工具，内部调用小米 MiMo 的 OpenAI 兼容接口 `POST /v1/chat/completions`，完成多模态（图像 + 文本）对话。

## 功能特性

- 支持图像 **URL** 与**本地文件路径**两种输入方式（本地图片自动编码为 data URL）。
- 内置 3 个工具：图像描述、多模态问答、OCR 文字识别。
- **API Key 通过 HTTP 请求头传入**，VPS 上无需存储密钥，更安全。
- 通过环境变量灵活配置 Base URL、默认模型与最大 token 数。
- 使用 `FastMCP` 构建，兼容任意 MCP 客户端（Claude Desktop、Cursor、TRAE 等）。
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

### 认证方式

| 模式 | API Key 传入方式 | 说明 |
| --- | --- | --- |
| stdio（本地） | 环境变量 `MIMO_API_KEY` | 传统方式，Key 存在本地 .env 或环境变量中 |
| streamable-http（远程） | **HTTP 请求头 `X-Mimo-Api-Key`** | Key 由客户端持有，VPS 上无需存储 |

HTTP 模式下，客户端在连接 MCP 服务器时通过 headers 传入 API Key，服务器从请求头读取并转发给 MiMo API，VPS 本身不保存任何密钥。

### 1. 安装依赖

```bash
cd mimo-vision-mcp
pip install -r requirements.txt
```

### 2. 配置（stdio 模式）

复制 `.env.example` 为 `.env` 并填入你的小米 MiMo API Key（HTTP 模式下可跳过此步）：

```bash
copy .env.example .env   # Windows
# 编辑 .env，设置 MIMO_API_KEY=你的Key
```

| 环境变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `MIMO_API_KEY` | stdio 模式必填 | - | 小米 MiMo API Key（HTTP 模式可由请求头替代） |
| `MIMO_BASE_URL` | 否 | `https://api.xiaomimimo.com/v1` | API 根地址 |
| `MIMO_MODEL` | 否 | `mimo-v2.5` | 默认模型 |
| `MIMO_MAX_TOKENS` | 否 | `1024` | 默认最大生成 token 数 |
| `MCP_TRANSPORT` | 否 | `stdio` | 传输模式：`stdio` 或 `streamable-http` |
| `MCP_HOST` | 否 | `0.0.0.0` | HTTP 模式监听地址 |
| `MCP_PORT` | 否 | `8000` | HTTP 模式监听端口 |
| `UPLOAD_DIR` | 否 | `/tmp/mimo-uploads` | 上传文件目录 |
| `UPLOAD_TTL` | 否 | `1800` | 上传文件过期时间（秒），默认 30 分钟 |
| `CLEANUP_INTERVAL` | 否 | `300` | 清理检查间隔（秒），默认 5 分钟 |

### 3. 启动服务器

```bash
python server.py
```

默认通过标准输入输出（stdio）模式与 MCP 客户端通信，供客户端配置为本地 MCP 服务器运行。

## 配置到 MCP 客户端

### stdio 模式（本地）

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

### streamable-http 模式（远程，API Key 通过请求头传入）

```json
{
  "mcpServers": {
    "mimo-vision": {
      "url": "http://<你的VPS_IP>:8000/mcp",
      "headers": {
        "X-Mimo-Api-Key": "你的MiMo_API_Key"
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

### 4. `upload_image` 上传本地图片（远程模式专用）

将本地图片编码为 base64 后上传到服务器，返回可直接用于其他工具的 URL。上传的图片 **30 分钟后自动删除**。

- `image_data`: base64 编码的图片数据，可带 `data:image/xxx;base64,` 前缀（必填）
- `filename`: 可选，原始文件名，用于推断图片格式

**使用场景**：远程部署时，客户端有本地图片文件需要识别。

## 图片上传（HTTP 模式）

远程部署时，服务器提供多种方式上传本地图片，返回 URL 后即可调用识别工具。

### 方式一：curl 上传文件（推荐）

```bash
curl -X POST http://<VPS_IP>:18253/upload -F "file=@/path/to/image.jpg"
# 返回: {"url":"http://<VPS_IP>:18253/uploads/xxx.jpg","path":"/tmp/mimo-uploads/xxx.jpg"}
```

### 方式二：base64 JSON 上传

```bash
curl -X POST http://<VPS_IP>:18253/upload/base64 \
  -H "Content-Type: application/json" \
  -d '{"image":"data:image/jpeg;base64,/9j/4AAQ...","ext":".jpg"}'
```

### 方式三：MCP upload_image 工具

通过 MCP 协议直接上传 base64 图片（适合 AI 客户端调用）。

### 自动清理

- 上传的文件保存在 `/tmp/mimo-uploads/`（可通过 `UPLOAD_DIR` 环境变量配置）
- 每 5 分钟检查一次，自动删除超过 30 分钟的文件
- 容器重启时自动清空所有上传文件
- 可通过 `UPLOAD_TTL`（过期秒数）和 `CLEANUP_INTERVAL`（检查间隔秒数）环境变量调整

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

#### 1. 创建 `docker-compose.yml`

在你的 VPS 上创建一个目录，放入 `docker-compose.yml`：

```yaml
services:
  mimo-vision-mcp:
    image: ghcr.io/samonysh/mimo-vision-mcp:latest
    ports:
      - "8000:8000"
    environment:
      # API Key 由客户端通过请求头 X-Mimo-Api-Key 传入，VPS 上无需配置
      - MIMO_BASE_URL=https://api.xiaomimimo.com/v1
      - MIMO_MODEL=mimo-v2.5
      - MIMO_MAX_TOKENS=1024
      - MCP_TRANSPORT=streamable-http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8000
    restart: unless-stopped
```

> VPS 上无需配置任何 API Key，密钥由客户端在连接时通过请求头传入。

#### 2. 启动服务

```bash
docker compose up -d
```

服务将在 `http://<你的VPS_IP>:8000` 上运行，MCP 端点为 `http://<你的VPS_IP>:8000/mcp`。

#### 3. 查看日志

```bash
docker compose logs -f
```

### 方式二：本地构建

```bash
docker compose up -d --build
```

### 连接远程 MCP 服务器

在支持远程 MCP 的客户端中配置（API Key 通过请求头传入，VPS 不存储密钥）：

```json
{
  "mcpServers": {
    "mimo-vision": {
      "url": "http://<你的VPS_IP>:8000/mcp",
      "headers": {
        "X-Mimo-Api-Key": "你的MiMo_API_Key"
      }
    }
  }
}
```

### CI/CD 自动构建

推送到 `main` 分支或打 `v*` 标签时，GitHub Actions 会自动：

1. 构建 `linux/amd64` + `linux/arm64` 双架构镜像
2. 推送到 `ghcr.io/samonysh/mimo-vision-mcp`
3. 自动打 `latest`、版本号等标签

镜像地址：`ghcr.io/samonysh/mimo-vision-mcp:latest`