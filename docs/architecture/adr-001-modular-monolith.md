# ADR 001: Modular Monolith

Status: accepted for 3.0

Siming remains one deployable application. The eight business modules expose
application contracts and keep `domain -> application -> interfaces` ownership.
Infrastructure implements application ports and is connected only by the
composition root.

This preserves offline installation, SQLite portability, MCP compatibility, and
simple recovery while allowing each use case to migrate independently.
