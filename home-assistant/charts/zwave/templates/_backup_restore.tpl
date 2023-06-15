{{/* Define the backup and restore job */}}
{{- define "zwave.backup_restore.job" -}}
spec:
  automountServiceAccountToken: true
  serviceAccountName: {{ index .Values "app-template" "global" "fullnameOverride" }}
  restartPolicy: OnFailure
  containers:
    - name: backup
      image: alpine:3.18
      imagePullPolicy: IfNotPresent
      command:
        - /scripts/backup_restore.sh
      envFrom:
        - secretRef:
            name: zwave-js-backup
      volumeMounts:
        - name: scripts
          mountPath: /scripts
      resources:
        requests:
          cpu: 100m
          memory: 128Mi
        limits:
          cpu: 1
          memory: 256Mi
  volumes:
    - name: scripts
      configMap:
        name: zwave-js-scripts
        defaultMode: 511
{{- end -}}
