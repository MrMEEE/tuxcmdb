# tuxcmdb-api curl examples

This file shows practical `curl` examples for creating attributes, creating assets, assigning attributes to assets, and removing assignments.

## Prerequisites

- API is running on `http://127.0.0.1:8080`
- You have an API user (example below uses `admin`)
- `curl` is installed

Optional helper variables:

```bash
API="http://127.0.0.1:8080"
AUTH="admin:your-password"
```

Health check (no auth):

```bash
curl "$API/health"
```

Auth check:

```bash
curl -u "$AUTH" "$API/ok"
```

## 1) Create attributes

Create a `location` attribute:

```bash
curl -u "$AUTH" -X POST "$API/v1/attributes" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "location",
    "data_type": "string",
    "description": "Rack location"
  }'
```

Create an `owner` attribute:

```bash
curl -u "$AUTH" -X POST "$API/v1/attributes" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "owner",
    "data_type": "string",
    "description": "Team or person responsible"
  }'
```

List attributes:

```bash
curl -u "$AUTH" "$API/v1/attributes"
```

Tip: extract attribute IDs from output and save them for later:

```bash
LOCATION_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="location") | .id')
OWNER_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="owner") | .id')
echo "LOCATION_ATTR_ID=$LOCATION_ATTR_ID OWNER_ATTR_ID=$OWNER_ATTR_ID"
```

## 2) Create an asset

Create an asset with hostname `srv-web-01`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets" \
  -H "Content-Type: application/json" \
  -d '{
    "hostname": "srv-web-01"
  }'
```

List assets:

```bash
curl -u "$AUTH" "$API/v1/assets"
```

Tip: extract asset ID:

```bash
ASSET_ID=$(curl -s -u "$AUTH" "$API/v1/assets?q=srv-web-01" | jq '.[0].id')
echo "ASSET_ID=$ASSET_ID"
```

## 3) Assign attributes to the asset

Assign `location=dc1-rack22`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d "{\"attribute_id\": $LOCATION_ATTR_ID, \"value\": \"dc1-rack22\"}"
```

Assign `owner=platform-team`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d "{\"attribute_id\": $OWNER_ATTR_ID, \"value\": \"platform-team\"}"
```

Show the asset with currently assigned attributes:

```bash
curl -u "$AUTH" "$API/v1/assets/$ASSET_ID"
```

## 4) Remove an assignment

Remove the `owner` assignment from the asset:

```bash
curl -u "$AUTH" -X DELETE "$API/v1/assets/$ASSET_ID/attributes/$OWNER_ATTR_ID"
```

Verify current assignments:

```bash
curl -u "$AUTH" "$API/v1/assets/$ASSET_ID"
```

## 5) Find assets by attribute

Find assets with attribute name `location` containing `rack22`:

```bash
curl -u "$AUTH" "$API/v1/assets/by-attribute?attribute_name=location&value=rack22"
```

Find assets by attribute ID instead of name:

```bash
curl -u "$AUTH" "$API/v1/assets/by-attribute?attribute_id=$LOCATION_ATTR_ID&value=dc1"
```

## Common errors

- `401 Invalid credentials`: wrong username/password
- `404 Asset not found`: wrong `ASSET_ID`
- `404 Attribute not found`: wrong `attribute_id`
- `409 Attribute already exists`: duplicate attribute name
- `409 Asset hostname already exists`: duplicate hostname
- `409 Asset is decommissioned`: cannot assign/remove attributes on decommissioned asset
