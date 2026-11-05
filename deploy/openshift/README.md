# OKD / OpenShift deployment

These namespace-scoped OpenShift Templates deploy the server components only:

- [server.yaml](server.yaml): a UBI 10 Python 3.12 S2I build, API and WebUI
  Deployments, Services, edge-TLS Routes, generated application secrets and a
  WebUI PVC.
- [database.yaml](database.yaml): either MariaDB 11.8 or PostgreSQL 16, a
  database PVC, generated database credentials and database builds.

All runtime images and builders are based on **UBI 10**. The public UBI
repositories do not include the database server packages. Therefore a
Docker-strategy bootstrap build installs signed packages from the upstream
MariaDB or PostgreSQL RPM repositories into UBI 10. A subsequent **S2I build**
adds the database initialization/run scripts to that builder. No RHEL base
images, Red Hat subscriptions, `anyuid` SCC, or root runtime containers are
required. The PostgreSQL repository must support the cluster's architecture;
the database builders have been smoke-tested on x86_64.

The API and WebUI share one S2I image with the dependencies of both services;
they run as separate pods. Their container-only settings leave local/RPM
configuration unchanged. WebUI runs Daphne (including WebSockets), with
`DEBUG=False`, a generated Django secret, explicit allowed host/CSRF origin,
secure cookies, and pre-collected static files served by WhiteNoise.
Compressed, non-manifest static storage accommodates the bundled vendor files'
missing source maps.

## Prerequisites

- An OKD/OpenShift cluster with Builds, the internal image registry and ingress
  enabled, and an authenticated `oc` client.
- Permission to create namespace-scoped workloads, Routes, PVCs, ImageStreams,
  and Docker/Source BuildConfigs. Builds need the cluster's standard builder
  service-account permissions; restricted runtime SCCs are sufficient.
- A default dynamically provisioned StorageClass supporting ReadWriteOnce.
  Alternatively edit the PVCs to specify your StorageClass before processing.
- DNS names under the cluster ingress domain. Edge Routes use the ingress
  certificate; install your own route certificates if using other domains.
- Build egress to Git, `registry.access.redhat.com`, PyPI, the UBI repositories,
  and the selected upstream database RPM repository.
- Commit/push these files to the Git branch used for the builds. Set
  `SOURCE_REPOSITORY_URL` to your fork and `SOURCE_REPOSITORY_REF` to that branch
  or a commit SHA in **both** templates. `main` is only suitable after these
  deployment files have been published there.

## Initial installation

Run from the repository root. Select or create a dedicated project:

```bash
oc new-project tuxcmdb
```

### 1. Select ONE database

MariaDB (default):

```bash
oc process --local -f deploy/openshift/database.yaml \
  -p SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git \
  -p SOURCE_REPOSITORY_REF=main \
  -p DB_BACKEND=mariadb -p DB_PORT=3306 \
  -p DATABASE_VOLUME_SIZE=10Gi \
  | oc create -f -
```

Or PostgreSQL:

```bash
oc process --local -f deploy/openshift/database.yaml \
  -p SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git \
  -p SOURCE_REPOSITORY_REF=main \
  -p DB_BACKEND=postgresql -p DB_PORT=5432 \
  -p DATABASE_VOLUME_SIZE=10Gi \
  | oc create -f -
```

Do not install both into the same project: they deliberately share the
`tuxcmdb-database` resource names. Backend and port must be selected together.
Database name/user default to `tuxcmdb`. Custom names and database passwords
must contain only ASCII letters, digits and underscores; the user must not be
`root` or `postgres`, and user/database lengths are limited to 32/63 characters.
Generated passwords meet these constraints. The administrator password lives
in a separate Secret and is not injected into the API/WebUI pods.

Two BuildConfigs are intentional: `tuxcmdb-database-builder` installs database
packages into UBI 10; `tuxcmdb-database` uses that image for S2I assembly.
Only the builder starts on configuration creation. The S2I build waits for
the builder ImageStreamTag to become available, and its resulting
ImageStreamTag updates the Deployment:

```bash
oc get builds -w
# After tuxcmdb-database-builder and tuxcmdb-database builds complete:
oc rollout status deployment/tuxcmdb-database --timeout=10m
```

### Recovering failed initial builds

`provided context directory does not exist: deploy/openshift/database` means
the Git commit cloned by the build does not contain these deployment files.
Processing a local template does not upload the local source tree. Commit and
push the deployment files, including `.s2i`, to the selected repository/ref
before retrying. Confirm the new commit contains the database directory.

An S2I build reporting a missing `from` image cannot start until the builder
image exists. Older versions of this template also triggered the S2I build on
configuration creation, before its builder was available. Update just that
BuildConfig in the current project; do not recreate Secrets or PVCs:

```bash
oc patch bc/tuxcmdb-database --type=merge \
  -p '{"spec":{"triggers":[{"type":"ImageChange"}]}}'
```

If you pushed to the already configured ref (for example `main`), retry:

```bash
oc start-build tuxcmdb-database-builder --follow
# Its successful output automatically triggers tuxcmdb-database.
oc get builds -w
```

If the files were pushed to a different branch, update **both** database
BuildConfigs first (replace `openshift-deploy` with your branch):

```bash
oc patch bc/tuxcmdb-database --type=merge \
  -p '{"spec":{"source":{"git":{"ref":"openshift-deploy"}}}}'
oc patch bc/tuxcmdb-database-builder --type=merge \
  -p '{"spec":{"source":{"git":{"ref":"openshift-deploy"}}}}'
```

The builder's ConfigChange trigger may start it when you patch its ref.
Check `oc get builds` before manually starting another build. Also select the
same published ref when creating the server template.

### 2. Install API and WebUI

Replace the example hostnames with real names under your ingress domain:

```bash
oc process --local -f deploy/openshift/server.yaml \
  -p SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git \
  -p SOURCE_REPOSITORY_REF=main \
  -p WEBUI_HOST=cmdb.apps.example.com \
  -p API_HOST=cmdb-api.apps.example.com \
  | oc create -f -

oc logs -f bc/tuxcmdb-server
oc rollout status deployment/tuxcmdb-api --timeout=10m
oc rollout status deployment/tuxcmdb-webui --timeout=10m
oc get routes
```

The API waits up to 120 seconds for its database, runs Alembic migrations,
reconciles API extension tables/defaults, and creates the initial administrator
if that username does not exist. Restarts do not reset existing users'
passwords, privileges or active status. The WebUI migrates its own persistent
SQLite database, used for Django sessions and per-user UI preferences.
CMDB data and login users reside in the selected server database.

Retrieve the generated initial credentials only in a private terminal:

```bash
oc get secret tuxcmdb-server \
  -o jsonpath='{.data.TUXCMDB_ADMIN_USER}' | base64 -d; echo
oc get secret tuxcmdb-server \
  -o jsonpath='{.data.TUXCMDB_ADMIN_PASSWORD}' | base64 -d; echo
```

Log in at `https://cmdb.apps.example.com`. Check the external API:

```bash
curl --fail https://cmdb-api.apps.example.com/health
curl --fail --user admin https://cmdb-api.apps.example.com/v1/assets
```

The second command prompts for the password. Agents should use the API Route.
The WebUI talks to the API's internal Service, not the public Route.

## Updates and operation

**Do not re-process and apply templates with fresh generated secrets to an
existing installation.** That would rotate database credentials and encryption
keys without updating persisted database users/encrypted values. Initial
installation uses `oc create` intentionally. For declarative/GitOps management,
render once, securely preserve the generated values, and manage secrets
separately. Never commit rendered secrets to Git.

After publishing application changes to the configured source ref:

```bash
oc start-build tuxcmdb-server --follow
```

To pick up database script changes without changing packages:

```bash
oc start-build tuxcmdb-database --follow
```

To update UBI/database packages, rebuild the database builder; its image trigger
then starts the database S2I build:

```bash
oc start-build tuxcmdb-database-builder --follow
```

BuildConfig source refs can be changed with `oc patch`, or edit the existing
objects using your configuration-management workflow. The Python base image is
configurable through `PYTHON_IMAGE`; pin base images/RPM repositories and Python
dependencies to reviewed versions/digests for reproducible production builds.
There is no scheduled rebuild of external base images.

All workloads intentionally use **one replica** and **Recreate**:

- API startup performs schema changes, so do not run concurrent API startups.
- The database is a single primary on a ReadWriteOnce PVC, not an HA cluster.
- WebUI has a persistent SQLite session/preference database and in-memory
  Channels layer. Scaling it requires a shared database and a shared channel
  layer, not merely increasing `replicas`.

Expect brief downtime during updates. Startup probes allow up to ten minutes
for initialization/migration. Database readiness executes an authenticated
query; API readiness checks `/health`; WebUI probes send the configured Host.
SQLAlchemy pools check connections before reuse so requests can reconnect after
a database pod restart.

The database is internal-only. External HTTP is redirected to HTTPS, and the
WebUI Route supports long-lived WebSocket connections. Edge TLS does **not**
encrypt pod-to-pod traffic. Configure network isolation and/or internal TLS to
match your environment. Do not expose application pods directly to untrusted
clients: Django trusts the router's forwarded scheme/host headers.

Back up both PVCs and all Secrets before upgrades. Preserve the LDAP bind
password key: losing/rotating it prevents decryption of existing LDAP bind
passwords. Database Secret edits do not change existing database passwords;
rotate credentials in the database itself and update dependent pods together.
Do not switch database backends or major versions on an existing data PVC.
Migrate data into a fresh deployment/project instead.

Database initialization writes a completion marker only after success. If a
pod reports incomplete initialization, inspect logs and recover/restore that
PVC rather than repeatedly attempting initialization over partial data.
The namespace's assigned UID must remain stable for an existing database PVC.
Normal pod restarts in the same project satisfy this; cross-project restore
requires ownership handling by the storage administrator.

## Validation

The focused runtime/template regression tests can be run with the project
environment (template tests require `oc`, but no login):

```bash
.venv/bin/python -m unittest discover -s tests -p test_openshift.py -v
```

Both custom database images, application assembly, migrations against both
backends, arbitrary-UID startup, API health/authentication, WebUI login/static
serving and persistence should also be checked when updating these files.
