FROM golang@sha256:3d699e4d15d0f8f13c9195c0632a16702b8cbdece2955af1c23b37ae5d55a253

COPY repo/ /opt/task/repo/
COPY env/solve-task /usr/local/bin/solve-task
RUN chmod 0555 /usr/local/bin/solve-task \
    && test -f /opt/task/repo/README

WORKDIR /workspace/repo
