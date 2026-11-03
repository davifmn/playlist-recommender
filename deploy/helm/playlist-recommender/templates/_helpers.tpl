{{- define "recommender.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "recommender.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "recommender.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "recommender.image" -}}
{{- $root := index . 0 -}}{{- $name := index . 1 -}}
{{- printf "%s/playlist-recommender-%s:%s" $root.Values.image.registry $name ($root.Values.image.tag | default $root.Chart.AppVersion) -}}
{{- end -}}

{{- define "recommender.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}{{ include "recommender.fullname" . }}{{- else -}}default{{- end -}}
{{- end -}}

{{/* Environment shared by the API and the trainer. */}}
{{- define "recommender.storageEnv" -}}
- name: RECSYS_STORE
  value: s3
- name: RECSYS_BUCKET
  value: {{ .Values.storage.bucket | quote }}
- name: AWS_DEFAULT_REGION
  value: {{ .Values.storage.region | quote }}
{{- $endpoint := .Values.storage.endpointUrl -}}
{{- if .Values.s3Emulator.enabled }}{{ $endpoint = printf "http://%s-s3:5000" (include "recommender.fullname" .) }}{{ end }}
{{- if $endpoint }}
- name: RECSYS_S3_ENDPOINT_URL
  value: {{ $endpoint | quote }}
{{- end }}
{{- $secret := .Values.storage.existingSecret -}}
{{- if .Values.s3Emulator.enabled }}{{ $secret = printf "%s-s3-credentials" (include "recommender.fullname" .) }}{{ end }}
{{- if $secret }}
- name: AWS_ACCESS_KEY_ID
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: AWS_ACCESS_KEY_ID } }
- name: AWS_SECRET_ACCESS_KEY
  valueFrom: { secretKeyRef: { name: {{ $secret }}, key: AWS_SECRET_ACCESS_KEY } }
{{- end }}
{{- end -}}

{{- define "recommender.trainerEnv" -}}
{{ include "recommender.storageEnv" . }}
- name: RECSYS_MIN_SUPPORT
  value: {{ .Values.trainer.minSupport | quote }}
- name: RECSYS_MIN_CONFIDENCE
  value: {{ .Values.trainer.minConfidence | quote }}
- name: RECSYS_MAX_LEN
  value: {{ .Values.trainer.maxLen | quote }}
{{- end -}}

{{/* Pod spec of a training run, shared by the CronJob and the on-deploy Job. */}}
{{- define "recommender.trainerPod" -}}
serviceAccountName: {{ include "recommender.serviceAccountName" . }}
restartPolicy: Never
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- if .Values.demoData.enabled }}
initContainers:
  - name: seed-demo-data
    image: {{ include "recommender.image" (list . "trainer") }}
    imagePullPolicy: {{ .Values.image.pullPolicy }}
    args: ["seed-demo", "--create-bucket", "--playlists", {{ .Values.demoData.playlists | quote }}]
    env: {{- include "recommender.storageEnv" . | nindent 6 }}
    securityContext: {{- toYaml .Values.securityContext | nindent 6 }}
    volumeMounts: [{ name: tmp, mountPath: /tmp }]
{{- end }}
containers:
  - name: trainer
    image: {{ include "recommender.image" (list . "trainer") }}
    imagePullPolicy: {{ .Values.image.pullPolicy }}
    args: ["run"]
    env: {{- include "recommender.trainerEnv" . | nindent 6 }}
    resources: {{- toYaml .Values.trainer.resources | nindent 6 }}
    securityContext: {{- toYaml .Values.securityContext | nindent 6 }}
    volumeMounts: [{ name: tmp, mountPath: /tmp }]
volumes:
  - name: tmp
    emptyDir: {}
{{- end -}}
