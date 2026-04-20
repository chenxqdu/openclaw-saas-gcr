{{/*
Chart name (truncated).
*/}}
{{- define "openclaw-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Uses release name, falls back to chart name.
Truncated to 63 chars (K8s name limit).
*/}}
{{- define "openclaw-platform.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label value.
*/}}
{{- define "openclaw-platform.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "openclaw-platform.labels" -}}
helm.sh/chart: {{ include "openclaw-platform.chart" . }}
{{ include "openclaw-platform.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (used in matchLabels + service selector).
*/}}
{{- define "openclaw-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openclaw-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "openclaw-platform.serviceAccountName" -}}
{{ include "openclaw-platform.fullname" . }}
{{- end }}
