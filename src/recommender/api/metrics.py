"""Prometheus metrics exposed on /metrics (scraped by a ServiceMonitor in the cluster)."""

from prometheus_client import Counter, Gauge, Histogram, Info

REQUESTS = Counter(
    "recommender_http_requests_total", "HTTP requests", ["route", "method", "status"]
)
LATENCY = Histogram(
    "recommender_http_request_duration_seconds",
    "Request latency",
    ["route"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
RECOMMENDATIONS_EMPTY = Counter(
    "recommender_empty_recommendations_total",
    "Requests where no rule matched (a signal that the model is stale or too strict)",
)
MODEL_RELOADS = Counter("recommender_model_reloads_total", "Model reload attempts", ["result"])
MODEL_RULES = Gauge("recommender_model_rules", "Number of rules in the served model")
MODEL_INFO = Info("recommender_model", "Version of the served model")
