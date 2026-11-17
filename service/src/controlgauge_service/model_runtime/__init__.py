"""Local model runtime (Sprint 5).

The service runs the official ``llama-server`` binary as a supervised
subprocess (PD-024) and talks to it over authenticated loopback HTTP
(D-017). See ``docs/MODEL_RUNTIME.md`` and ``docs/DEV_MODELS.md``.
"""
