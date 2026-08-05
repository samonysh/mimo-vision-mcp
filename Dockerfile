FROM python:3.12-slim

WORKDIR /app

# 安装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY server.py .

# 暴露 HTTP 端口
EXPOSE 8000

# 默认使用 streamable-http 传输模式
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

CMD ["python", "server.py"]
