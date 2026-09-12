"""Traffic scope identifiers shared by builders and export clients."""
DEFAULT_TRAFFIC_SCOPE_ID = "global"
DENSE_TRAFFIC_SCOPES = {
    "east_dense": ("demo_3", "demo_5", "demo_6", "demo_9"),
    "west_dense": ("demo_14", "demo_15", "demo_19"),
}
TRAFFIC_SCOPE_LABELS = {
    "global": "Global official demand",
    "east_dense": "East dense area",
    "west_dense": "West dense area",
}
SUPPORTED_TRAFFIC_SCOPE_IDS = (
    DEFAULT_TRAFFIC_SCOPE_ID,
    *DENSE_TRAFFIC_SCOPES,
)
