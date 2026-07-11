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
    "allow_multiple": true,
    "description": "Primary management IPv4"
  }'
```

`allow_multiple=true` is useful for attributes like IP addresses, where an asset may need more than one active value.

List attributes:

```bash
curl -u "$AUTH" "$API/v1/attributes"
```

Optional: extract attribute IDs if you still want ID-based operations:

```bash
LOCATION_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="location") | .id')
OWNER_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="owner") | .id')
MGMT_IP_ATTR_ID=$(curl -s -u "$AUTH" "$API/v1/attributes" | jq '.[] | select(.name=="management_ip") | .id')
echo "LOCATION_ATTR_ID=$LOCATION_ATTR_ID OWNER_ATTR_ID=$OWNER_ATTR_ID MGMT_IP_ATTR_ID=$MGMT_IP_ATTR_ID"
```

## 2) Create an asset

Create an asset with assetname `srv-web-01`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets" \
  -H "Content-Type: application/json" \
  -d '{
    "assetname": "srv-web-01"
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

You can also use assetname directly in the URL for attribute assignment routes:

```bash
ASSET_NAME="srv-web-01"
```

## 3) Assign attributes to the asset

There are three supported request styles when assigning attributes.

### A) By `attribute_name` (recommended)

Assign `location=dc1-rack22`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"attribute_name": "location", "value": "dc1-rack22"}'
```

Same assignment using assetname in URL:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_NAME/attributes" \
  -H "Content-Type: application/json" \
  -d '{"attribute_name": "location", "value": "dc1-rack22"}'
```

Assign `owner=platform-team`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"attribute_name": "owner", "value": "platform-team"}'
```

### B) By `attribute_id` (backward-compatible)

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d "{\"attribute_id\": $LOCATION_ATTR_ID, \"value\": \"dc1-rack22\"}"
```

### C) Shorthand payload (key = attribute name)

Same result as `attribute_name`, but shorter:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"management_ip": "10.44.1.21"}'
```

Assign a valid IPv4 (`management_ip=10.44.1.21`):

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"attribute_name": "management_ip", "value": "10.44.1.21"}'
```

Assign a second active IPv4 when `allow_multiple=true`:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"management_ip": "10.44.1.22"}'
```

Because `management_ip` is configured with `allow_multiple=true`, both IPs remain active.

Try an invalid IPv4 (`999.44.1.21`) to see validation:

```bash
curl -u "$AUTH" -X POST "$API/v1/assets/$ASSET_ID/attributes" \
  -H "Content-Type: application/json" \
  -d '{"attribute_name": "management_ip", "value": "999.44.1.21"}'
```

Expected result: `400 Value '999.44.1.21' is not valid for data_type 'ipv4'`.

Show the asset with currently assigned attributes:

```bash
curl -u "$AUTH" "$API/v1/assets/$ASSET_ID"
```

## 4) Remove an assignment

Remove the `owner` assignment from the asset:

```bash
curl -u "$AUTH" -X DELETE "$API/v1/assets/$ASSET_ID/attributes/owner"
```

Same removal using assetname in URL:

```bash
curl -u "$AUTH" -X DELETE "$API/v1/assets/$ASSET_NAME/attributes/owner"
```

Remove one specific repeated IP value from `management_ip`:

```bash
curl -u "$AUTH" -X DELETE "$API/v1/assets/$ASSET_ID/attributes/management_ip?value=10.44.1.22"
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
- `409 Asset assetname already exists`: duplicate assetname
- `409 Asset is decommissioned`: cannot assign/remove attributes on decommissioned asset
