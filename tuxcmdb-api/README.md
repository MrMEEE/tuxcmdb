# tuxcmdb-api

A separate minimal API service for TuxCMDB.

## Scope (current)

- FastAPI with HTTP Basic auth, Pydantic response models, and automatic request validation.
- Protected `v1` endpoints for attributes and assets.
- One unauthenticated endpoint: `GET /health`.
- Dedicated install script that creates `apiusers` table in the existing TuxCMDB database.
- API install also ensures `assets.active` exists for decommission support.

## Install

```bash
cd tuxcmdb-api
pip install -r requirements.txt
python ../tuxcmdb-api.py --create-user admin
```

To install and immediately start the API:

```bash
python ../tuxcmdb-api.py --create-user admin --run
```

The `tuxcmdb-api.py` installer will also auto-create `../.venv` and install missing dependencies from `requirements.txt` when needed.

By default, `tuxcmdb-api.py` reads DB URL from `conf/database.yaml`, creates `apiusers`, optionally creates the user, and writes `tuxcmdb-api/conf/api.yaml`.

## Run

```bash
cd tuxcmdb-api
python app.py
```

## Quick Test

Health (no auth):

```bash
curl http://127.0.0.1:8080/health
```

Protected endpoint:

```bash
curl -u admin:your-password http://127.0.0.1:8080/ok
```

## Endpoints

All endpoints except `/health` require HTTP Basic auth.

Attributes:

- `GET /v1/datatypes`
- `POST /v1/attributes`
- `GET /v1/attributes?q=...&limit=...&offset=...`
- `PATCH /v1/attributes/{attribute_id}`
- `DELETE /v1/attributes/{attribute_id}`

Assets:

- `POST /v1/assets`
- `GET /v1/assets?q=...&active=true|false`
- `GET /v1/assets/{asset_id}`
- `PATCH /v1/assets/{asset_id}`
- `POST /v1/assets/{asset_id}/attributes`
- `DELETE /v1/assets/{asset_id}/attributes/{attribute_id}`
- `POST /v1/assets/{asset_id}/decommission`
- `GET /v1/assets/by-attribute?attribute_name=...&value=...`

`GET /v1/assets` and `GET /v1/assets/{asset_id}` include each asset's current assigned attributes (latest assignment state where `assigned=true`).

`POST /v1/assets/{asset_id}/attributes` validates `value` against the attribute's `data_type` from the `datatypes` table.
Default datatypes are seeded by installer/startup: `string`, `integer`, `numeric`, `ipv4`, `ipv6`, `subnet`, and `boolean`.
