FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f AS sqlite-build

# Debian's SQLite 3.46.1 predates the WAL-reset corruption fix. Build a pinned
# upstream release so every container uses the same fixed SQLite library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc make libc6-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp/sqlite-build
RUN python -c 'import hashlib, pathlib, urllib.request; url="https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz"; data=urllib.request.urlopen(url, timeout=120).read(); assert hashlib.sha3_256(data).hexdigest()=="b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4"; pathlib.Path("sqlite.tar.gz").write_bytes(data)' \
    && tar -xzf sqlite.tar.gz \
    && cd sqlite-autoconf-3540100 \
    && CFLAGS="-O2 -DSQLITE_MAX_VARIABLE_NUMBER=250000 -DSQLITE_MAX_DEFAULT_PAGE_SIZE=32768 -DSQLITE_SECURE_DELETE=1 -DSQLITE_ENABLE_FTS3_PARENTHESIS=1 -DSQLITE_ENABLE_FTS3_TOKENIZER=1" \
       ./configure --prefix=/usr/local --disable-static --all --fts3 --column-metadata \
    && make -j2 \
    && make install

FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f

COPY --from=sqlite-build /usr/local/lib/libsqlite3.so* /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib
RUN ldconfig \
    && python -c 'import sqlite3; assert sqlite3.sqlite_version == "3.54.1", sqlite3.sqlite_version; print("SQLite", sqlite3.sqlite_version)'

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY watchlist.json ./watchlist.json

RUN pip install --no-cache-dir --retries 5 --timeout 60 uv==0.7.20 \
    && uv sync --locked --no-dev

EXPOSE 8899

CMD ["aipa-api", "serve", "--host", "0.0.0.0", "--port", "8899"]
