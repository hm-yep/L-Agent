# ------------------------------------------------------------
# 凌零 · API 服务镜像（只跑 FastAPI，不跑 PyQt5 桌宠 UI）
#   docker build -t lingling-api .
#   docker run -e LINGLING_API_TOKEN=换成一个随机串 -p 8000:8000 lingling-api
#
# 设计取舍：
#   * 基础依赖用 requirements-api.txt，不把 Qt / 文档工厂 / torch 拖进镜像；
#   * RAG / 语音为构建期可选项（体积以 GB 计，默认不装）；
#   * 以非 root 用户运行，数据目录可写；
#   * 服务未配置访问令牌时只接受回环请求，对外暴露必须设置 LINGLING_API_TOKEN。
# ------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG WITH_RAG=0
ARG WITH_VOICE=0

COPY requirements*.txt ./
RUN pip install -r requirements-api.txt \
    && if [ "$WITH_RAG" = "1" ]; then pip install -r requirements-rag.txt; fi \
    && if [ "$WITH_VOICE" = "1" ]; then pip install -r requirements-voice.txt; fi

COPY . .

RUN groupadd -r lingling \
    && useradd -r -g lingling -d /app lingling \
    && mkdir -p /app/data/uploads /app/data/logs \
    && chown -R lingling:lingling /app

USER lingling

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
