FROM python:3.12-slim AS builder

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# 更新源
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential pkg-config patchelf cmake libportaudio2 portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

# 使用固定版本工具链，避免构建期间通过脚本下载浮动的 stable。
COPY --from=rust:1.90-slim /usr/local/cargo /usr/local/cargo
COPY --from=rust:1.90-slim /usr/local/rustup /usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup
ENV PATH=/usr/local/cargo/bin:$PATH

# 固定 uv 版本；从 PyPI 安装，避免额外依赖 GHCR 镜像拉取。
RUN pip install --no-cache-dir uv==0.7.22

# 设置工作目录
WORKDIR /app

# 复制项目文件
COPY . .

# 安装锁定依赖（保持 pyproject.toml 与 uv.lock 一致）
RUN uv sync --locked --no-install-project --no-editable

# 构建 Rust 扩展并安装
RUN uv run --no-sync maturin build --locked --release --manifest-path native/Cargo.toml \
    && uv pip install --no-deps target/wheels/*.whl \
    && uv pip uninstall maturin


FROM python:3.12-slim

WORKDIR /app

# 更新源
RUN apt-get update && apt-get install -y --no-install-recommends \
    libzstd1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY main.py .
COPY config.py config.native-wake.py config.mixed-wake.py ./
COPY core ./core
COPY scripts ./scripts


# Ensure sherpa_onnx can locate onnxruntime shared library at runtime.
RUN set -eux; \
    ort_dir="/app/.venv/lib/python3.12/site-packages/onnxruntime/capi"; \
    if [ ! -f "$ort_dir/libonnxruntime.so" ]; then \
      ln -s "$(basename "$(ls "$ort_dir"/libonnxruntime.so.* | head -n 1)")" "$ort_dir/libonnxruntime.so"; \
    fi

ENV LD_LIBRARY_PATH=/app/.venv/lib/python3.12/site-packages/onnxruntime/capi

# 暴露 API 服务器端口
EXPOSE 9092

# 与命令行共用配置解析、检查和关键词准备；使用镜像内项目解释器。
CMD ["/app/.venv/bin/python", "main.py"]
