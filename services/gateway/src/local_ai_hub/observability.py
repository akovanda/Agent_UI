from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "local_ai_hub_http_requests_total",
    "Gateway HTTP requests",
    ["method", "path", "status"],
)
CHAT_REQUESTS = Counter(
    "local_ai_hub_chat_requests_total",
    "Chat completion requests routed by profile and backend",
    ["profile", "backend", "stream"],
)
ROUTE_DECISIONS = Counter(
    "local_ai_hub_route_decisions_total",
    "Automatic and explicit routing decisions",
    ["requested", "profile", "backend"],
)
REQUEST_SECONDS = Histogram(
    "local_ai_hub_chat_request_seconds",
    "End-to-end chat request duration",
    ["profile", "backend", "stream"],
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
MODEL_LOAD_SECONDS = Histogram(
    "local_ai_hub_model_load_seconds",
    "llama.cpp model transition duration",
    ["model", "result"],
    buckets=(1, 2.5, 5, 10, 20, 30, 60, 120, 240, 480),
)
GPU_IN_FLIGHT = Gauge(
    "local_ai_hub_gpu_in_flight",
    "Requests currently holding a GPU execution lease",
)
GPU_WAITERS = Gauge(
    "local_ai_hub_gpu_waiters",
    "Requests currently waiting for a GPU execution lease",
)
MEMORY_RETRIEVALS = Counter(
    "local_ai_hub_memory_retrievals_total",
    "Memory retrieval operations",
    ["profile", "result"],
)
