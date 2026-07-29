FROM python@sha256:5dc6f84b5e97bfb0c90abfb7c55f3cacc2cb6687c8f920b64a833a2219875997

RUN apt-get update \
    && apt-get install -y --no-install-recommends git=1:2.39.5-0+deb12u2 \
    && rm -rf /var/lib/apt/lists/*

COPY repo/ /opt/task/repo/
RUN test -f /opt/task/repo/README
WORKDIR /workspace/repo
