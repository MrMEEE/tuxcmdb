FROM registry.access.redhat.com/ubi10/ubi:latest

COPY mariadb/MariaDB.repo /etc/yum.repos.d/MariaDB.repo
RUN dnf -y --setopt=install_weak_deps=False install MariaDB-server MariaDB-client nss_wrapper-libs && \
    dnf clean all

ENV HOME=/opt/app-root/src
LABEL io.openshift.s2i.scripts-url="image:///usr/libexec/s2i"
COPY assemble /usr/libexec/s2i/assemble
COPY entrypoint /usr/libexec/s2i/run
RUN mkdir -p /opt/app-root/src /var/lib/tuxcmdb && \
    chgrp -R 0 /opt/app-root /var/lib/tuxcmdb && \
    chmod -R g=u /opt/app-root /var/lib/tuxcmdb && \
    chmod 755 /usr/libexec/s2i/assemble /usr/libexec/s2i/run
WORKDIR /opt/app-root/src
USER 1001
EXPOSE 3306
CMD ["/usr/libexec/s2i/run"]
