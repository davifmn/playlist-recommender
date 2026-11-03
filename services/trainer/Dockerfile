# syntax=docker/dockerfile:1
# Build context: repository root.  docker build -f services/trainer/Dockerfile .
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[trainer]"

FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/davifmn/playlist-recommender" \
      org.opencontainers.image.description="Playlist recommender batch trainer"
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
USER 10001
# mlxtend imports matplotlib, which wants a writable config dir; /tmp is an emptyDir in k8s.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/matplotlib
ENTRYPOINT ["recommender-train"]
CMD ["run"]
