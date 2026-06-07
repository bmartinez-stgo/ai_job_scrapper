{{- define "job-scraper.app.name" -}}job-scraper-app{{- end }}
{{- define "job-scraper.llm.name" -}}job-scraper-llm{{- end }}

{{- define "job-scraper.labels" -}}
app.kubernetes.io/managed-by: Helm
app.kubernetes.io/part-of: job-scraper
{{- end }}

{{- define "job-scraper.app.selectorLabels" -}}
app: job-scraper-app
{{- end }}

{{- define "job-scraper.llm.selectorLabels" -}}
app: job-scraper-llm
{{- end }}
