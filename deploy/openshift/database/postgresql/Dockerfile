FROM registry.access.redhat.com/ubi10/ubi:latest

RUN dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-$(uname -m)/pgdg-redhat-repo-latest.noarch.rpm && \
    dnf -y --setopt=install_weak_deps=False install postgresql16-server nss_wrapper-libs && \
    dnf clean all

ENV PATH="/usr/pgsql-16/bin:${PATH}" HOME=/opt/app-root/src
LABEL io.openshift.s2i.scripts-url="image:///usr/libexec/s2i"
COPY assemble /usr/libexec/s2i/assemble
COPY entrypoint /usr/libexec/s2i/run
RUN mkdir -p /opt/app-root/src /var/lib/tuxcmdb && \
    chgrp -R 0 /opt/app-root /var/lib/tuxcmdb && \
    chmod -R g=u /opt/app-root /var/lib/tuxcmdb && \
    chmod 755 /usr/libexec/s2i/assemble /usr/libexec/s2i/run
WORKDIR /opt/app-root/src
USER 1001
EXPOSE 5432
CMD ["/usr/libexec/s2i/run"]
