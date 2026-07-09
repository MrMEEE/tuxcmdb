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

## Builtin validators

Current builtin validators:

- `ipv4`: validates IPv4 addresses using Python `ipaddress`
- `ipv6`: validates IPv6 addresses using Python `ipaddress`
- `subnet`: validates CIDR subnets like `10.0.0.0/24` or `2001:db8::/64`
- `boolean`: validates `true/false`, `1/0`, `yes/no`, `on/off`
- `integer`: validates signed integer values

List all registered datatypes:

```bash
curl -u "$AUTH" "$API/v1/datatypes"
```

List only datatypes that use builtin validators:

```bash
curl -s -u "$AUTH" "$API/v1/datatypes" | jq '.[] | select(.builtin_validator != null)'
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

Create a `management_ip` attribute with `ipv4` datatype:

```bash
curl -u "$AUTH" -X POST "$API/v1/attributes" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "management_ip",
    "data_type": "ipv4",
    "description": "Primary management IPv4"
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
MGMT_IP_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="management_ip") | .id')
echo "LOCATION_ATTR_ID=$LOCATION_ATTR_ID OWNER_ATTR_ID=$OWNER_ATTR_ID MGMT_IP_ATTR_ID=$MGMT_IP_ATTR_ID"
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

Assign a valid IPv4 (`management_ip=10.44.1.21`):

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d "{\"attribute_id\": $MGMT_IP_ATTR_ID, \"value\": \"10.44.1.21\"}"
```

Try an invalid IPv4 (`999.44.1.21`) to see validation:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d "{\"attribute_id\": $MGMT_IP_ATTR_ID, \"value\": \"999.44.1.21\"}"
```

Expected result: `400 Value '999.44.1.21' is not valid for data_type 'ipv4'`.

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
