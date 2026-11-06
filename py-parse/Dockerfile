# py-parse gRPC 解析服务镜像
#
# 只包含 py-parse 本身（gRPC server + chunking），文档抽取依赖独立的 MinerU
# 服务（见 docker/compose.yaml 的 mineru-api）。两者通过 compose 网络通信。

FROM python:3.10-slim

WORKDIR /app

# 先装依赖以利用 Docker 层缓存。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码 + 已生成的 gRPC 桩代码 + proto（含 .py 包结构）。
COPY py_parse/ ./py_parse/
COPY proto/ ./proto/

EXPOSE 50051

# MinerU 地址默认指向 compose 网络内的 mineru-api 服务；本地直跑可覆盖。
ENV MINERU_URL=http://mineru-api:8000

CMD ["python", "-m", "py_parse"]
