FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates python3 python3-pip ruby ruby-dev binutils xz-utils && \
    gem install --no-document fpm -v 1.15.1 && rm -rf /var/lib/apt/lists/*
ENV PIP_BREAK_SYSTEM_PACKAGES=1 HOME=/tmp
USER 1001
WORKDIR /work/src
