# OKD / OpenShift deployment

These namespace-scoped OpenShift Templates deploy the server components only:

- [server.yaml](server.yaml): independent UBI 10 Python 3.12 S2I builds, API and WebUI
  Deployments, Services, edge-TLS Routes, generated application secrets and a
  WebUI PVC.
- [database.yaml](database.yaml): either MariaDB 11.8 or PostgreSQL 16, a
  database PVC, generated database credentials and database builds.
- [agents.yaml](agents.yaml) (optional): containerized agent package builds and
  an updater pod that publishes downloads to shared persistent storage.

All long-running server images and database builders are based on **UBI 10**. The public UBI
repositories do not include the database server packages. Therefore a
Docker-strategy bootstrap build installs signed packages from the upstream
MariaDB or PostgreSQL RPM repositories into UBI 10. A subsequent **S2I build**
adds the database initialization/run scripts to that builder. No RHEL base
images, Red Hat subscriptions, `anyuid` SCC, or root runtime containers are
required. The PostgreSQL repository must support the cluster's architecture;
the database builders have been smoke-tested on x86_64.

The API and WebUI have separate S2I BuildConfigs, ImageStreams, Deployments and
pods: `tuxcmdb-api` and `tuxcmdb-webui`. Each image installs its own component's
dependencies plus shared core dependencies. Only the WebUI collects static
files. Building one component updates only its own Deployment.
Their container-only settings leave local/RPM
configuration unchanged. WebUI runs Daphne (including WebSockets), with
`DEBUG=False`, a generated Django secret, explicit allowed host/CSRF origin,
secure cookies, and pre-collected static files served by WhiteNoise.
Compressed, non-manifest static storage accommodates the bundled vendor files'
missing source maps.

Agent downloads are **disabled by default**. Optional reusable toolchain images
use UBI 8/9/10 for RPMs and Debian 12 for the DEB. Image builds install tools
only, never build agent packages. A UBI 10 updater periodically checks Git and
launches short-lived Kubernetes Jobs to build and publish packages. API/WebUI
runtime images remain UBI 10 whether or not downloads are enabled.

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
- If enabling agent downloads, build egress also needs the UBI 8/9 registries
  and repositories, Docker Hub (Debian), Debian package repositories and RubyGems.
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

If database S2I assembly fails with `cp: preserving times ... Operation not
permitted`, update to the corrected assemble script and rebuild
`tuxcmdb-database-builder` first. The builder embeds that script, so retrying
only `tuxcmdb-database` would still use the old copy operation. The corrected
script copies without preserving metadata and changes permissions only on
the copied contents, not the root-owned work directory.

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

oc logs -f bc/tuxcmdb-api
oc logs -f bc/tuxcmdb-webui
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

### 3. Optional agent downloads

Skip this step to opt out. The default WebUI will report that no agent
downloads are available; it has no dependency on agent builds.

Commit and push these files first. Install the optional template **after**
the server using a **ReadWriteMany (RWX)** StorageClass. The WebUI, updater
and build Jobs can run on different nodes:

```bash
oc process --local -f deploy/openshift/agents.yaml \
  -p SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git \
  -p SOURCE_REPOSITORY_REF=main \
  -p AGENTS_STORAGE_CLASS=your-rwx-storageclass \
  -p AGENT_CHECK_INTERVAL=3600 \
  | oc create -f -

# These build reusable tools, not agent packages:
oc logs -f bc/tuxcmdb-agents
oc get builds -w
oc rollout status deployment/tuxcmdb-agents-updater --timeout=10m

# Rebuild the WebUI once from the updated source for the new build controls:
oc start-build tuxcmdb-webui --follow
# Add the optional connection and read-only download volume:
oc patch deployment/tuxcmdb-webui --type=strategic \
  -p "$(cat deploy/openshift/agents-webui-patch.yaml)"
oc rollout status deployment/tuxcmdb-webui --timeout=10m
```

Wait for all five toolchain/updater image builds to complete; until then the
updater logs missing ImageStreamTags and retries. The template creates:

- `tuxcmdb-agents`: a UBI 10 controller image with Git, Python and publishing
  scripts, but no agent sources or packages.
- `tuxcmdb-agent-builder-el8`, `el9`, `el10` and `debian`: reusable package tools.
- A 5 GiB RWX PVC (`AGENTS_VOLUME_SIZE`), internal Service, generated shared
  credential, and one updater replica with a Recreate strategy.
- A dedicated service account and namespace Role allowing only listing/creating
  Jobs and reading the five named ImageStreamTags. Jobs themselves have no API
  token. The WebUI needs no Kubernetes permissions.

Neither application BuildConfig is changed. The separate
[WebUI patch](agents-webui-patch.yaml) preserves the session PVC, mounts downloads
read-only, and configures server-side updater communication. No public updater
Route is created. The shared credential is sent only between server processes
over the internal cluster network; it is not exposed to browsers.

#### Automatic and manual package builds

The updater checks the configured repository/ref on startup and every
`AGENT_CHECK_INTERVAL` seconds (default 3600, minimum 60). If its commit differs
from the published commit it creates one Job, pinned to that commit and the
current toolchain image digests. The Job checks out source, builds all four
Linux targets in their matching distro containers, then publishes the complete
set including PowerShell. No nested container engine or privileged SCC is used.
The source repository must be trusted: its packaging scripts execute in Jobs.
Private repositories requiring authentication inside the running pods are not
configured by this template.

The same [shared packaging helper](../../tools/build-agent-package.sh) is used
by Jobs and GitHub Actions. Successful builds produce:

- `tuxcmdb-agent-VERSION-1.el8.noarch.rpm` (legacy EL8 source, system requests).
- `tuxcmdb-agent-VERSION-1.el9.noarch.rpm`.
- `tuxcmdb-agent-VERSION-1.el10.noarch.rpm`.
- `tuxcmdb-agent_VERSION-1.debian_all.deb`.
- `tuxcmdb-agent.ps1` and `tuxcmdb-agent-VERSION.ps1`.

OpenShift derives the version
from the nearest reachable release tag matching `vMAJOR.MINOR.PATCH`, followed
by the number of commits since that tag: three commits after `v0.2.26` becomes
`0.2.26.3`. A build exactly at that tag becomes `0.2.26.0`. A new release tag
resets the counter; prerelease and unrelated-branch tags are ignored. This is
Git ancestry distance, not the highest version tag anywhere in the repository.

Git metadata lives only in the Job's temporary work volume, not in any image.
Missing release tags or failed history fetches fail the Job rather than guessing
a version. GitHub release builds keep their unsuffixed release-tag version.

RPMs and DEBs
preserve the existing launcher, systemd units and installation/removal scripts.
EL9/10 and Debian vendor pure-Python dependencies so the packages remain
architecture-independent.

The publishing container validates the version, commit, complete package set and SHA-256
checksums, then copies the artifacts to a new release directory on the PVC.
Only after verification does it atomically switch the relative `current`
symlink. A failed publication leaves the previous downloads available.
The WebUI reads `current` on each request. Downloads are served through
`/agents/` and existing stable `latest` aliases. **Agent updates do not rebuild
or restart the WebUI, API or updater, and require no new image build.**

Signed-in users with write access see **Build agents now** and **Agent build
settings** on the Agents page, but only in the OpenShift installation mode and
when the optional updater responds. The POST action is CSRF-protected and uses
the same Job path as automatic checks; an already active Job is reused. A
manual request rebuilds even an unchanged source commit. Anonymous/read-only
users retain public download access but cannot trigger builds or change settings.
Build status, build-request results and errors are shown in on-page modals.
**Agent build settings** opens a modal to edit version retention; validation
errors stay in that modal and saving does not navigate away from Agents.

Inspect builds and failures:

```bash
oc logs deployment/tuxcmdb-agents-updater
oc get jobs,pods -l component=agent-package-build
# Substitute the Job pod name and stage (checkout, el8, el9, el10, debian, publish):
oc logs POD_NAME -c el10
```

Job stages are sequential, with a one-hour deadline, no automatic Job retries,
and completed Job cleanup after one day. Failures are shown on the Agents page
and leave the existing downloads intact. The updater rechecks running Job status
every 30 seconds; failed builds can be retried manually or at the next source
check. Runtime pods need egress to Git and PyPI, in addition to the image-build
egress listed above.

#### Version retention

**Agent build settings → Versions to keep** defaults to **3**, including the
current version. It accepts positive whole numbers. Settings are persisted in
`settings.json` on the agent PVC and survive updater/WebUI restarts. WebUI writes
settings through the authenticated updater Service, never directly to the PVC.

After publication, or after a setting change, the updater keeps the current
release plus the most recently published distinct versions up to the configured
count. Rebuilding the same version replaces that version rather than consuming
another slot. Older retained versioned files remain listed/downloadable; `latest`
always refers to the current release. Cleanup never runs while a build Job is
active. Already-open download streams can finish after a release is retired;
links to removed versions return 404.

Incomplete/unrecognized release directories, including pre-upgrade publications
without hidden version metadata, are deliberately not deleted automatically.
Monitor volume usage and remove these specific retired directories manually if
needed. Back up the PVC to preserve artifacts/settings across cluster recovery.

#### Updating an existing optional installation

Publish the updated source first. Preserve the existing PVC and any existing
`tuxcmdb-agents-updater` Secret. On the first upgrade from the older publisher,
create that Secret once using the Secret object rendered from the template.
For subsequent applications, filter Secrets out:

```bash
# Run ONLY if this optional Secret does not already exist:
oc process --local -f deploy/openshift/agents.yaml \
  -p AGENTS_STORAGE_CLASS=your-rwx-storageclass \
  | jq '{apiVersion:"v1",kind:"List",items:[.items[] | select(.kind == "Secret")]}' \
  | oc create -f -

# Preserve the existing shared credential when updating other resources:
oc process --local -f deploy/openshift/agents.yaml \
  -p AGENTS_STORAGE_CLASS=your-rwx-storageclass \
  | jq '{apiVersion:"v1",kind:"List",items:[.items[] | select(.kind != "Secret")]}' \
  | oc apply -f -
```

Use your existing repository/ref, StorageClass and size parameters. Rebuild
`tuxcmdb-agents` once and rebuild the WebUI once for the new controls. Reapply
the WebUI patch above. Building the updater image no longer builds packages;
the running updater handles new commits through Jobs. Rebuild the four toolchain
images only when their dependencies/Dockerfiles change. Preserve the mount patch
in your deployment configuration when replacing the WebUI Deployment.

#### Disabling agent downloads after enabling them

Remove only the download volume mount from the WebUI; this restarts the WebUI
once and leaves its session volume untouched:

```bash
oc set volume deployment/tuxcmdb-webui --remove --name=agent-downloads
oc set env deployment/tuxcmdb-webui TUXCMDB_AGENT_UPDATER_URL- TUXCMDB_AGENT_UPDATER_TOKEN- TUXCMDB_AGENT_RELEASES_DIR-
oc rollout status deployment/tuxcmdb-webui --timeout=10m
oc scale deployment/tuxcmdb-agents-updater --replicas=0
```

Allow any running build Job to finish first. Optionally remove the five optional
BuildConfigs/ImageStreams, updater Deployment/Service and its RBAC afterward.
Keep the PVC unless intentionally discarding artifacts/settings.

#### Migrating from embedded agent downloads

If you previously applied an older `agents.yaml` that modified the WebUI
BuildConfig, first restore the base WebUI BuildConfig (only that object) using
`server.yaml`. This removes its artifact image input and agent-triggered builds.
Rebuild the WebUI once from the updated source, then apply the optional storage
template and mount patch above. Do not reapply generated Secrets or recreate
your session/database PVCs. Subsequent agent builds update only the updater pod.

Use your existing repository/ref and Python image parameters when restoring it:

```bash
oc process --local -f deploy/openshift/server.yaml \
  -p SOURCE_REPOSITORY_URL=https://github.com/MrMEEE/tuxcmdb.git \
  -p SOURCE_REPOSITORY_REF=main \
  -p WEBUI_HOST=cmdb.apps.example.com \
  -p API_HOST=cmdb-api.apps.example.com \
  | jq '{apiVersion:"v1",kind:"List",items:[.items[] |
      select(.kind == "BuildConfig" and .metadata.name == "tuxcmdb-webui")]}' \
  | oc apply -f -
oc get builds -w
# The restored ConfigChange trigger starts the one-time WebUI rebuild.
```

## Updates and operation

### Remote agent installation

The Agents page includes **Install agent** for signed-in users with write access,
independently of the optional build controls. Published Linux packages are
required. See the [remote installation guide](../../agent/README.md#remote-linux-installation).
The WebUI pod must resolve target hostnames and reach the targets on TCP 22; target hosts need
API Route and package-repository access. Restrict WebUI egress to authorized
management networks as appropriate.

The installer stores per-host progress and SSH known-host keys under
`/opt/app-root/src/webui-data/agent-installs` on the existing session PVC.
SSH passwords are not stored. Do not restart/rebuild WebUI during an installation.
The server template defaults the registration URL to `https://${API_HOST}`,
not the separate WebUI hostname. On an existing installation set it explicitly
without reapplying generated Secrets:

```bash
oc set env deployment/tuxcmdb-webui \
  TUXCMDB_AGENT_INSTALL_SERVER_URL="https://$(oc get route tuxcmdb-api -o jsonpath='{.spec.host}')"
```

Rebuild the WebUI once after publishing installer changes to install its
Paramiko SSH dependency. No API or updater rebuild is needed.

All BuildConfigs retain only one successful and one failed build. To apply
these limits to existing BuildConfigs without reprocessing templates or Secrets:

```bash
oc -n tuxcmdb get buildconfigs -o name | while IFS= read -r buildconfig; do
  oc -n tuxcmdb patch "$buildconfig" --type=merge \
    -p '{"spec":{"successfulBuildsHistoryLimit":1,"failedBuildsHistoryLimit":1}}'
done
```

OpenShift handles build-history pruning; these limits do not control the
downloadable agent version retention setting or package-build Job history.

**Do not re-process and apply templates with fresh generated secrets to an
existing installation.** That would rotate database credentials and encryption
keys without updating persisted database users/encrypted values. Initial
installation uses `oc create` intentionally. For declarative/GitOps management,
render once, securely preserve the generated values, and manage secrets
separately. Never commit rendered secrets to Git.

After publishing application changes to the configured source ref:

```bash
oc start-build tuxcmdb-api --follow
oc start-build tuxcmdb-webui --follow
```

Either component can be rebuilt independently; running both commands is not
required when only one component changed.

### Migrating from the shared application build

Publish these changes first. Render the updated template using the same source
ref and route hostnames, but apply only BuildConfigs, ImageStreams and
Deployments so existing Secrets and PVCs are preserved:

```bash
oc process --local -f deploy/openshift/server.yaml \
  -p SOURCE_REPOSITORY_REF=main \
  -p WEBUI_HOST=cmdb.apps.example.com \
  -p API_HOST=cmdb-api.apps.example.com \
  | jq '{apiVersion:"v1",kind:"List",items:[.items[] |
      select(.kind == "BuildConfig" or .kind == "ImageStream" or .kind == "Deployment")]}' \
  | oc apply -f -

oc logs -f bc/tuxcmdb-api
oc logs -f bc/tuxcmdb-webui
oc rollout status deployment/tuxcmdb-api --timeout=10m
oc rollout status deployment/tuxcmdb-webui --timeout=10m
```

Set `SOURCE_REPOSITORY_URL` as well if using a fork. Once both Deployments
successfully use their new images, the obsolete shared build and image stream
can be removed (the `tuxcmdb-server` Secret must be kept):

```bash
oc delete bc/tuxcmdb-server is/tuxcmdb-server
```

### Database rebuilds

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
