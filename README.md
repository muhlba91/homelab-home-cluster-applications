# Homelab: Kubernetes Home Cluster - Applications

[![Build status](https://img.shields.io/github/actions/workflow/status/muhlba91/homelab-kubernetes-home-applications/pipeline.yml?style=for-the-badge)](https://github.com/muhlba91/homelab-kubernetes-home-applications/actions/workflows/pipeline.yml)
[![License](https://img.shields.io/github/license/muhlba91/homelab-kubernetes-home-applications?style=for-the-badge)](LICENSE.md)

This repository contains applications deployed on the `home-cluster` via [ArgoCD](https://argo-cd.readthedocs.io/en/stable/) using [GitOps](https://opengitops.dev).

---

## Bootstrapping

A Kubernetes cluster needs to be bootstrapped with the [Cilium CNI](https://cilium.io) and ArgoCD with an `Application` pointing to this repository.

For [ksops](https://github.com/viaduct-ai/kustomize-sops) and ArgoCD to decrypt the initial secrets for configuring the [External Secrets Operator](http://external-secrets.io) using [Doppler](http://doppler.com), a [Google Cloud Service Account](https://cloud.google.com/docs/authentication#service-accounts) with access to the correct KMS key needs to be set in the `argocd` namespace. You can check out [`infrastructure/charts/argocd/values.yaml`](infrastructure/charts/argocd/values.yaml) on how this secret is passed to ArgoCD.

ArgoCD will then manage Cilium, itself, and all applications as defined in this repository.

---

## ArgoCD App-of-Apps

The repository layout follows ArgoCD's [app-of-apps pattern](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/).

The first ArgoCD `Application` being defined needs to reference [`app-of-apps/values.yaml`](app-of-apps/values.yaml) and the environment specific `values-<ENVIRONMENT>.yaml` files.

These are bootstrapping the main ArgoCD projects and applications, referring to the respective `<PROJECT>/applications/values[-<ENVIRONMENT>].yaml` files:

- [`infrastructure`](#infrastructure): core cluster infrastructure, like Cilium and ArgoCD
- [`core`](#core-applications): core applications, like [cert-manager](http://cert-manager.io) and [traefik](https://traefik.io)
- [`applications`](#user-applications): (user) applications running on the cluster/network
- [`home-assistant`](#home-assistant): [Home Assistant](http://home-assistant.io) related applications

Each of these applications follows the app-of-apps pattern again using subcharts defined in the respective `charts` directory.

### Additional Helm Value Files

In addition to the included `values[-<ENVIRONMENT].yaml` files, ArgoCD `Application`s load additonal Helm value files from an external repository defined with `global.spec.values.repoURL`.

For example, values only defined in the external repository are ingress domains.

## Library Charts

### Applications

To support bootstrapping these app-of-apps `Application`s, the library chart [applications](library/charts/applications) creates the ArgoCD `Project` and `Application` definitions based on the provided values.

---

## Hosted Services

### Infrastructure

The following applications are defined in [`infrastructure/charts`](infrastructure/charts).

- [x] [ArgoCD](https://argo-cd.readthedocs.io/en/stable/) - Manages the applications deployed on the cluster using GitOps.
- [x] [Cilium](https://cilium.io) - Provides the cluster CNI.
- [x] [CSI NFS Driver](https://github.com/kubernetes-csi/csi-driver-nfs/tree/master) - Exposes the NAS' NFS storage as a Kubernetes `StorageClass`.
- [x] [Descheduler](https://github.com/kubernetes-sigs/descheduler) - Finds pods to be evicted for optimizing node usage.
- [x] [External Secrets Operator](http://external-secrets.io) - Synchronizes secrets from external stores to Kubernetes `Secret` objects.
- [x] [MetalLB](https://metallb.universe.tf) - Provides a Kubernetes network load balancer to expose Kubernetes `Service`s.
- [x] [Longhorn](https://longhorn.io) - Exposes local storage to Kubernetes `StorageClass`es.

#### Kustomizations

- [x] [External Secrets Stores](infrastructure/kustomizations/external-secrets-stores) - Deploys the required `ClusterSecretStore`s and Doppler [Service Tokens](https://docs.doppler.com/docs/service-tokens) as Kubernetes `Secret`s.

### Core Applications

The following applications are defined in [`core/charts`](core/charts).

- [x] [cert-manager](https://cert-manager.io) - Certificate management using ACME Let's Encrypt.
- [x] [External DNS with Google Cloud DNS integration](https://github.com/kubernetes-sigs/external-dns) - Creates DNS records in Google Cloud DNS domains for publicly reachable services.
- [x] [Traefik](https://traefik.io) - Exposes Kubernetes `Ingress` resources to the "outside world".

### (User) Applications

The following applications are defined in [`applications/charts`](applications/charts).

- [x] [AdGuard](https://adguard.com/en/adguard-home/overview.html) - DNS server with ad filtering/blocking capabilities.
- [x] [CoreDNS](https://coredns.io) - DNS resolver for internal, local only, domains.
- [x] [dnsmasq](https://thekelleys.org.uk/dnsmasq/doc.html) - IPv4 and IPv6 DHCP server.
- [x] [External DNS with CoreDNS/etcd integration](https://github.com/kubernetes-sigs/external-dns) - Creates DNS records in CoreDNS/etcs for internal, local only, reachable services.
- [x] External Services - Deploys Kubernetes `Service`s and `Ingress`es to local endpoints, and existing services outside of the cluster.
- [x] [Grafana](http://grafana.com) - Visualization of metrics, and other data.
- [x] [MinIO](https://min.io) - Local object storage.

### Home Assistant

The following applications are defined in [`home-assistant/charts`](home-assistant/charts).

- [x] [EMQX](https://www.emqx.io) - A MQTT broker.
- [x] [Home Assistant](https://home-assistant.io) - The Home Assistant instance.
- [x] [Node-RED](https://nodered.org) - Automation based on flows and Home Assistant data.
- [x] [Ring MQTT](https://github.com/tsightler/ring-mqtt) - Amazon Ring devices to MQTT bridge.
- [x] [Telegraf](https://www.influxdata.com/time-series-platform/telegraf/) - Forwards Home Assistant state changes to a local [InfluxDB](https://www.influxdata.com) instance.
- [x] [Z-Wave JS UI](https://github.com/zwave-js/zwave-js-ui) - Full featured Z-Wave Control Panel and MQTT Gateway.

#### Notes: Backup and Restore

Home Assistant related backup and restore is currently handled via S3 backups.

For most services, upon pod start an `initContainer` as well as a nightly `CronJob` are backing up data to an S3 bucket.
If no data is found in the Persistent Volume yet, the data from will be retrieved and copied over which results in a full restore.

The following services also have Git repositories to store their configuration which gets pulled in upon start:

- [Home Assistant](https://github.com/muhlba91/homelab-home-assistant-configuration)
  - Home Assistant also defines it's own backup method via a `trigger` and a `shell_command`, and doesn't rely on a `CronJob`.
- [Ring MQTT](https://github.com/muhlba91/homelab-ring-mqtt-configuration)

---

## Backup and Restore

No (cluster-wide) backup and restore has been implemented as of yet.

***Note:*** for Home Asisstant backup and restore, see the [corresponding section](#notes-backup-and-restore).

---

## Continuous Integration and Automations

- [GitHub Actions](https://docs.github.com/en/actions) are linting and templating all Helm charts.
- [Renovate Bot](https://github.com/renovatebot/renovate) is updating Helm (sub)charts and used container images in the `values.yaml` files, and GitHub Actions.
