{{- define "agent-ui.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agent-ui.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "agent-ui.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "agent-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "agent-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agent-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "agent-ui.secretName" -}}
{{- printf "%s-secrets" (include "agent-ui.fullname" .) -}}
{{- end -}}

{{- define "agent-ui.registryConfigMap" -}}
{{- if .Values.registryConfig.existingConfigMap -}}
{{- .Values.registryConfig.existingConfigMap -}}
{{- else -}}
{{- printf "%s-registry" (include "agent-ui.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "agent-ui.modelSourceVolumeName" -}}
{{- printf "model-%s" . | replace "_" "-" | trunc 63 | trimSuffix "-" -}}
{{- end -}}
