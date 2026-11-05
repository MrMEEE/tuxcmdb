FROM registry.access.redhat.com/ubi10/ubi:latest
RUN dnf install -y ca-certificates python3 python3-pip ruby ruby-devel rpm-build findutils && \
    gem install --no-document fpm -v 1.15.1 && dnf clean all
ENV HOME=/tmp
USER 1001
WORKDIR /work/src
