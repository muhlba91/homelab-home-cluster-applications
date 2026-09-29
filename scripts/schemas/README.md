# Local kubeconform schema overrides

Schemas here take precedence over the datree CRDs-catalog (see `../kubeconform.yaml`).
Only add a schema when the catalog is outdated for the version installed in the cluster.

| Schema | Reason | Source |
|---|---|---|
| `external-secrets.io/externalsecret_v1.json` | catalog lacks `creationPolicy: CreateOrMerge` | CRD `externalsecrets.external-secrets.io` (v1) from the vie cluster, external-secrets v2.11.0 |

Regenerate after an external-secrets upgrade (read-only against the cluster):

```bash
kubectl get crd externalsecrets.external-secrets.io -o json | python3 -c '
import json,sys
v=[x for x in json.load(sys.stdin)["spec"]["versions"] if x["name"]=="v1"][0]["schema"]["openAPIV3Schema"]
def conv(o):
    if isinstance(o,dict):
        if o.get("x-kubernetes-int-or-string"):
            o={k:w for k,w in o.items() if k not in("x-kubernetes-int-or-string","type")}; o["oneOf"]=[{"type":"string"},{"type":"integer"}]
        if "properties" in o and "additionalProperties" not in o and not o.get("x-kubernetes-preserve-unknown-fields"):
            o["additionalProperties"]=False
        return {k:conv(w) for k,w in o.items()}
    return [conv(x) for x in o] if isinstance(o,list) else o
v=conv(v); v["properties"]["apiVersion"]["enum"]=["external-secrets.io/v1"]; v["properties"]["kind"]["enum"]=["ExternalSecret"]
json.dump(v,open("scripts/schemas/external-secrets.io/externalsecret_v1.json","w"),indent=2,sort_keys=True)'
```

Remove the override once the catalog schema contains the needed fields.
