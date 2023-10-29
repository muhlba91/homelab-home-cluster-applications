# Homelab: Kubernetes Home Cluster - Applications

[![Build status](https://img.shields.io/github/actions/workflow/status/muhlba91/homelab-home-cluster-applications/pipeline.yml?style=for-the-badge)](https://github.com/muhlba91/homelab-home-cluster-applications/actions/workflows/pipeline.yml)
[![License](https://img.shields.io/github/license/muhlba91/homelab-home-cluster-applications?style=for-the-badge)](LICENSE.md)

This repository contains applications deployed on the `home-cluster` via [Flux](https://fluxcd.io) using [GitOps](https://opengitops.dev).

---

## Bootstrapping

A Kubernetes cluster needs to be bootstrapped with the [Cilium CNI](https://cilium.io) and Flux pointing to this repository.

For [ksops](https://github.com/viaduct-ai/kustomize-sops) and ArgoCD to decrypt the initial secrets for configuring the [External Secrets Operator](http://external-secrets.io) using [Doppler](http://doppler.com), a [Google Cloud Service Account](https://cloud.google.com/docs/authentication#service-accounts) with access to the correct KMS key needs to be set in the `flux` namespace.

***Attention:*** some applications will be automatically deployed, others not (yet).

---

## App-of-Apps

The repository follows the app-of-apps pattern.

The first Flux `Kustomization` being defined needs to reference [`app-of-apps/`](app-of-apps/).

These are bootstrapping the main Flux applications, referring to the respective `<PROJECT>/applications/` kosutomizations:

- [`infrastructure`](#infrastructure): core cluster infrastructure
- [`core`](#core-applications): core applications
- [`applications`](#user-applications): (user) applications running on the cluster/network
- [`home-assistant`](#home-assistant): [Home Assistant](http://home-assistant.io) related applications

Each of these applications follows the app-of-apps pattern again using sub-kustomizations defined in the respective application directories.

---

## Hosted Services

### Infrastructure

The following applications are defined in [`infrastructure/`](infrastructure/).

- [x] [Cilium](https://cilium.io) - Provides the cluster CNI.
- [ ] [CSI NFS Driver](https://github.com/kubernetes-csi/csi-driver-nfs/tree/master) - Exposes the NAS' NFS storage as a Kubernetes `StorageClass`.
- [x] [MetalLB](https://metallb.universe.tf) - Provides a Kubernetes network load balancer to expose Kubernetes `Service`s.
- [x] [Longhorn](https://longhorn.io) - Exposes local storage to Kubernetes `StorageClass`es.
- [x] [NFS CSI Driver](https://github.com/kubernetes-csi/csi-driver-nfs/tree/master) - Exposes NAS NFS storage to Kubernetes `StorageClass`es.
- [x] [External Secrets Operator](http://external-secrets.io) - Synchronizes secrets from external stores to Kubernetes `Secret` objects.
  - [x] [External Secrets Stores](infrastructure/external-secrets/) - Deploys the required `ClusterSecretStore`s and Doppler [Service Tokens](https://docs.doppler.com/docs/service-tokens) as Kubernetes `Secret`s.
- [x] [Traefik](https://traefik.io) - Exposes Kubernetes `Ingress` resources to the "outside world".

### Core Applications

The following applications are defined in [`core/`](core/).

- [x] [cert-manager](https://cert-manager.io) - Certificate management using ACME Let's Encrypt.
- [x] [External DNS with Google Cloud DNS integration](https://github.com/kubernetes-sigs/external-dns) - Creates DNS records in Google Cloud DNS domains for publicly reachable services.
- [x] [victoria-metrics-stack](https://github.com/VictoriaMetrics/helm-charts/tree/master/charts/victoria-metrics-k8s-stack) - Monitoring stack using [Victoria Metrics](https://victoriametrics.com).

### (User) Applications

The following applications are defined in [`applications/`](applications/).

- [x] [AdGuard](https://adguard.com/en/adguard-home/overview.html) - DNS server with ad filtering/blocking capabilities.
- [x] [CoreDNS](https://coredns.io) - DNS resolver for internal, local only, domains.
- [x] [dnsmasq](https://thekelleys.org.uk/dnsmasq/doc.html) - IPv4 and IPv6 DHCP server.
- [x] [External DNS with CoreDNS/etcd integration](https://github.com/kubernetes-sigs/external-dns) - Creates DNS records in CoreDNS/etcs for internal, local only, reachable services.
- [x] External Services - Deploys Kubernetes `Service`s and `Ingress`es to local endpoints, and existing services outside of the cluster.
- [x] [Grafana](http://grafana.com) - Visualization of metrics, and other data.
- [ ] [MinIO](https://min.io) - Local object storage.

### Home Assistant

The following applications are defined in [`home-assistant/`](home-assistant/).

- [x] [ecowitt2mqtt](https://github.com/bachya/ecowitt2mqtt) - Forwards data received from ecowitt devices to the MQTT broker.
- [x] [EMQX](https://www.emqx.io) - A MQTT broker.
- [x] [Home Assistant](https://home-assistant.io) - The Home Assistant instance.
- [x] [Node-RED](https://nodered.org) - Automation based on flows and Home Assistant data.
- [x] [Ring MQTT](https://github.com/tsightler/ring-mqtt) - Amazon Ring devices to MQTT bridge.
- [x] [Telegraf](https://www.influxdata.com/time-series-platform/telegraf/) - Forwards Home Assistant state changes to a local [InfluxDB](https://www.influxdata.com) instance.
- [x] [Z-Wave JS UI](https://github.com/zwave-js/zwave-js-ui) - Full featured Z-Wave Control Panel and MQTT Gateway.

#### Notes: Backup and Restore

Home Assistant related backup and restore is currently handled via S3 backups.

The following services implement an `initContainer` as well as a nightly `CronJob` to backup data to an S3 bucket. If no data is found in the Persistent Volume yet, the data from will be retrieved and copied over which results in a full restore.

- Ring MQTT

The following services use API calls to determine whether a backup or restore is necessary.

- Node-RED
- Home Assistant
- Z-Wave JS UI

The following services also have Git repositories to store their configuration which gets pulled in upon start.

- [Home Assistant](https://github.com/muhlba91/homelab-home-assistant-configuration)
  - Home Assistant also defines it's own backup method via a `trigger` and a `shell_command`, and doesn't rely on a `CronJob`.
- [Ring MQTT](https://github.com/muhlba91/homelab-ring-mqtt-configuration)

---

## Backup and Restore

No (cluster-wide) backup and restore has been implemented as of yet.

***Note:*** for Home Asisstant backup and restore, see the [corresponding section](#notes-backup-and-restore).

---

## Continuous Integration and Automations

- [GitHub Actions](https://docs.github.com/en/actions) are linting all YAML files.
- [Renovate Bot](https://github.com/renovatebot/renovate) is updating Helm releases and used container images in the `values.yaml` files, and GitHub Actions.
