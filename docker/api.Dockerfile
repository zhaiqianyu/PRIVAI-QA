# 浣跨敤杞婚噺绾ython鍩虹闀滃儚
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /uvx /bin/
COPY --from=node:20-slim /usr/local/bin /usr/local/bin
COPY --from=node:20-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node:20-slim /usr/local/include /usr/local/include
COPY --from=node:20-slim /usr/local/share /usr/local/share

# 璁剧疆宸ヤ綔鐩綍
WORKDIR /app

# 鐜鍙橀噺璁剧疆
ENV TZ=Asia/Shanghai \
    UV_PROJECT_ENVIRONMENT="/usr/local" \
    UV_COMPILE_BYTECODE=1 \
    UV_HTTP_TIMEOUT=300 \
    DEBIAN_FRONTEND=noninteractive

RUN npm install -g npm@latest && npm cache clean --force

# 璁剧疆浠ｇ悊鍜屾椂鍖猴紝鏇存崲闀滃儚婧愶紝瀹夎绯荤粺渚濊禆 - 鍚堝苟涓轰竴涓猂UN鍑忓皯灞傛暟
RUN set -ex \
    # (A) 璁剧疆鏃跺尯
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    # (B) 鏇挎崲娓呭崕婧?(閽堝 Debian Bookworm 鐨勬柊鐗堟牸寮?
    && sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && sed -i 's|security.debian.org/debian-security|mirrors.tuna.tsinghua.edu.cn/debian-security|g' /etc/apt/sources.list.d/debian.sources \
    # (C) 瀹夎蹇呰鐨勭郴缁熷簱
    && apt-get update \
    && apt-get install -y --no-install-recommends --fix-missing \
        curl \
        ffmpeg \
        libsm6 \
        libxext6 \
    # (D) 娓呯悊鍨冨溇锛屽噺灏忎綋绉?
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


# 澶嶅埗椤圭洰閰嶇疆鏂囦欢
COPY ../pyproject.toml /app/pyproject.toml
COPY ../.python-version /app/.python-version
COPY ../uv.lock /app/uv.lock


# 鎺ユ敹鏋勫缓鍙傛暟(濡傛灉鍑虹幇浠ｇ悊閿欒锛屽垯鎶婁笅闈㈠叧浜庣幆澧冨彉閲忕殑閮芥敞閲婃帀锛屽苟娉ㄩ噴鎺?dock-compose.yml 鐨?6-8 琛?
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""

# 璁剧疆鐜鍙橀噺锛堣繖浜涘€煎彲鑳芥槸绌虹殑锛?
ENV HTTP_PROXY=$HTTP_PROXY \
    HTTPS_PROXY=$HTTPS_PROXY \
    http_proxy=$HTTP_PROXY \
    https_proxy=$HTTPS_PROXY

# 濡傛灉缃戠粶杩樻槸涓嶅ソ锛屽彲浠ュ湪鍚庨潰娣诲姞 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen  --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 婵€娲昏櫄鎷熺幆澧冨苟娣诲姞鍒癙ATH
ENV PATH="/app/.venv/bin:$PATH"

# 澶嶅埗浠ｇ爜鍒板鍣ㄤ腑
COPY ../src /app/src
COPY ../server /app/server



