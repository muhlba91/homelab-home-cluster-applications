{{/* Define the backup and restore job */}}
{{- define "node-red.backup_restore.job" -}}
spec:
  automountServiceAccountToken: true
  serviceAccountName: {{ index .Values "app-template" "global" "fullnameOverride" }}
  restartPolicy: OnFailure
  containers:
    - name: backup
      image: python:3.11-alpine
      imagePullPolicy: IfNotPresent
      command:
        - /scripts/backup_restore.sh
      envFrom:
        - secretRef:
            name: node-red-backup
        - secretRef:
            name: node-red-credentials
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
        name: node-red-scripts
        defaultMode: 511
{{- end -}}
