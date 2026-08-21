# Security Audit Report — SCADA Digital Twin Platform

Date: 2026-08-21
Scope: Full codebase, dependencies, deployment defaults

## Findings Summary

| Category | Severity | Status | Description |
|----------|----------|--------|-------------|
| API Authentication | HIGH | MITIGATED | SCADA_API_TOKEN env var; empty by default (demo mode) |
| Rate Limiting | MEDIUM | IMPLEMENTED | CONTROL_RATE_LIMIT per IP, MAX_WS_CLIENTS |
| Modbus Exposure | HIGH | MITIGATED | MODBUS_ALLOWED_CLIENTS allowlist (empty = allow all) |
| Input Validation | LOW | PRESENT | Pydantic models validate all API inputs |
| Dependency Audit | INFO | NEEDS REVIEW | Dependencies should be scanned (pip-audit) |
| CORS | INFO | OK | No CORS middleware = same-origin only (safe default) |
| Secrets | INFO | OK | No hardcoded secrets; env vars for config |
| Audit Logging | LOW | IMPLEMENTED | JSONL audit log for all control actions |

## Recommendations

1. **For production deployment**: Always set SCADA_API_TOKEN and MODBUS_ALLOWED_CLIENTS
2. **Never expose Modbus port (502) to the public internet** — isolate on private field network
3. **Run pip-audit regularly** to check for known vulnerabilities in dependencies
4. **Use TLS** (Render provides this automatically via HTTPS)
5. **The simulation is NOT certified for real equipment control** — document this limitation clearly

## Default Security Posture

The application defaults to an **open demo mode** (no authentication, no Modbus allowlist) for development convenience. This is acceptable for:
- Local development
- Educational use
- Closed lab networks

For any network exposure:
- Set `SCADA_API_TOKEN=<strong-token>`
- Set `MODBUS_ALLOWED_CLIENTS=127.0.0.1` (or specific trusted IPs)
- Consider adding rate limiting middleware for high-traffic deployments

## Component Security

### PLC Logic
- Interlocks enforced at PLC level, not UI level
- E-stop has highest authority
- Sensor faults cascade to safe states
- Commands validated server-side before state changes

### State Management
- Module-level singleton (single-process by design)
- CLI refuses `--workers > 1` to prevent split-brain
- Field lock serializes Modbus access against sim loop

### WebSocket
- Per-client queue with backpressure (MAX_QUEUE=128)
- Slow clients dropped (queue full → client disconnected)
- Connection cap (MAX_WS_CLIENTS)

### Data Persistence
- SQLite with WAL mode for concurrent reads
- JSONL audit log (append-only)
- Leak events persisted (survives restart)
