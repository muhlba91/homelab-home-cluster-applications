# Restore Runbook

How data comes back after data loss or on a fresh cluster. Git holds every
manifest. The mechanisms below only restore **data**.

- **CloudNativePG** (immich, mealie, litellm, librechat RAG): barman-cloud WAL
  and daily base backup. [Automatic](#cloudnativepg-automatic).
- **Percona MongoDB** (librechat, omada-controller): PBM logical backup, daily.
  [Manual restore CR](#percona-mongodb-manual).
- **App PVCs** (immich `data`, mealie `data`, influxdb, grafana): Velero
  file-system backup (kopia), daily. [Manual Velero restore](#velero-manual).
- **Home Assistant, Z-Wave JS UI, Ring MQTT**: S3 backup via
  `CronJob`/`initContainer`/`Job`.
  [Self-healing](#s3-self-healing-home-assistant-stack).

> [!CAUTION]
> Never run a restore test that **archives** into the same CNPG
> `ObjectStore`/`serverName` while the real cluster runs. Use a separate
> `ObjectStore` (or path) for tests.

---

## CloudNativePG (automatic)

Each backed-up `Cluster` bootstraps with `bootstrap.recovery` from its own
`ObjectStore` (`<app>-postgresql-scaleway`). Recreating the `Cluster` (e.g. on
a fresh cluster) restores it to the latest archived WAL. No manual step is
needed.

- The commented `initdb` block in each `postgresql.yaml` shows how to
  bootstrap a **fully empty** database instead. Only use it when no backup
  should be restored.
- `bootstrap` is only evaluated when the cluster is created. Changing it on a
  running cluster has no effect.
- Credentials come from Vault via `managed.roles`, so app secrets stay valid
  across restores.

## Percona MongoDB (manual)

PBM writes logical backups to the `scaleway` storage of `librechat-mongodb` /
`omada-controller-mongodb`. The operator's users secret (`*-mongodb-secrets`)
comes from Vault, so restored system users match.

1. List backups: `kubectl get perconaservermongodbbackups -n <namespace>`.
2. Apply a `PerconaServerMongoDBRestore` pointing at the cluster and the
   backup:

   ```yaml
   apiVersion: psmdb.percona.com/v1
   kind: PerconaServerMongoDBRestore
   metadata:
     name: restore-<date>
     namespace: <namespace>
   spec:
     clusterName: <app>-mongodb
     backupName: <backup-name>
   ```

3. Watch `kubectl get perconaservermongodbrestores -n <namespace>` until
   `ready`, then check the app.

On a fresh cluster the backup objects don't exist yet. Use `backupSource`
(S3 destination + storage name) instead of `backupName`.

## Velero (manual)

Schedules live in
[`sites/vie/core/velero/schedules/`](../sites/vie/core/velero/schedules/).
Only pods annotated with `backup.velero.io/backup-volumes` have their volume
data backed up. Other PVCs in the namespace (e.g. CNPG) are included as empty
objects, so always restore with a selector.

1. `flux suspend ks <app> <app>-extensions`
2. Scale the workload to 0 and delete the target PVC.
3. Restore only the app's volume:

   ```bash
   velero restore create --from-backup <backup> \
     --include-resources persistentvolumeclaims,persistentvolumes,pods \
     --selector <app pod labels>
   ```

4. Wait for completion, then delete the restored standalone pod.
5. `flux resume ks <app> <app>-extensions`

## S3 self-healing (Home Assistant stack)

See [Home Assistant Backup](../README.md#home-assistant-backup). A fresh
(empty) instance restores itself from S3.

- **Z-Wave JS UI:** the `zwave-js-restore` `Job` runs once when the extensions
  are first applied and restores from S3 immediately. The daily
  `home-assistant-zwave-backup` `CronJob` runs the same script and also
  restores whenever authentication is still disabled (i.e. a fresh instance).
