#!/bin/bash

# $1 = Base URL (e.g., litellm.litellm:4000)
# $2 = API Key

curl -s "https://${1}/v1/models" \
  -H "Authorization: Bearer ${2}" | \
  jq -r '.data[].id' | sort | sed 's/^/- /'
