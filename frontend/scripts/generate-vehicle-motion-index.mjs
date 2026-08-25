// The event lane and vehicle motion indexes share one network parse and one
// SUMO-to-WGS84 conversion pass, so this entry point intentionally generates both.
await import('./generate-event-lane-position-index.mjs')
