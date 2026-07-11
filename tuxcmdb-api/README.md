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

Create a readonly API user:

```bash
python ../tuxcmdb-api.py --create-user readonly-user --readonly
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
Users with `readonly=true` can access read endpoints but cannot perform POST, PATCH, or DELETE operations.

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
- `POST /v1/assets/{asset_ref}/attributes` (`asset_ref` can be asset id or assetname)
- `DELETE /v1/assets/{asset_ref}/attributes/{attribute_ref}?value=...` (`asset_ref` and `attribute_ref` can be asset/attribute id or name)
- `POST /v1/assets/{asset_id}/decommission`
- `GET /v1/assets/by-attribute?attribute_name=...&value=...`

`GET /v1/assets` and `GET /v1/assets/{asset_id}` include each asset's current assigned attributes (latest assignment state where `assigned=true`).

`POST /v1/assets/{asset_id}/attributes` validates `value` against the attribute's `data_type` from the `datatypes` table.
You can identify the target attribute by either `attribute_name` (recommended) or `attribute_id` in the request body.
It also supports shorthand payload where the key is the attribute name, for example `{"management_ip": "10.44.1.21"}`.
If an attribute has `allow_multiple=true`, multiple active assignment rows are kept for the same asset and attribute. The default is `false`, which keeps only the latest active row.
`DELETE /v1/assets/{asset_ref}/attributes/{attribute_ref}` can remove by attribute name or id, and `?value=...` can target a specific repeated value for `allow_multiple=true` attributes.
Default datatypes are seeded by installer/startup: `string`, `integer`, `numeric`, `ipv4`, `ipv6`, `subnet`, and `boolean`.
