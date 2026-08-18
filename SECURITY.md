# Security Policy

## Supported versions

Only the latest `main` branch is supported. There are no long-lived release
branches for this educational project.

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue:

1. Open a GitHub **Security Advisory** against this repository, or
2. Email a maintainer with a description and, if possible, a minimal repro.

Do not include live credentials, API keys, or production plant data in a
report.

## Scope and intent

This project is an **educational simulation** and an engineering prototype.
It is *not* a certified PLC, a safety-rated runtime, or production SCADA
software. Known, accepted limitations include:

- No authentication/authorisation on the REST or WebSocket endpoints.
- Plain TCP/HTTP — no TLS.
- The Modbus TCP server binds all interfaces and applies no device
  authentication (matching standard Modbus TCP).
- A single-threaded process with no hard real-time guarantee.

Deploying it on an untrusted network, or driving real hardware from it, is
out of scope and not recommended. Treat any finding that only affects
production-hardening (auth, TLS, auditing) as a feature request rather than
a vulnerability in a demonstrator.

Please do report issues that would make the *simulation itself* unsafe or
misleading, such as a bypass of PLC interlocks, a frontend that fabricates
process values, or a control-loop defect.
