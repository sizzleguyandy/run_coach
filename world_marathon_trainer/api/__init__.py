"""FastAPI layer for the World Marathon Trainer engine.

Thin HTTP wrapper over the pure engine. Contains NO training logic — it only
validates input, calls the engine, and serialises output. n8n / the agent call
these endpoints; they never import the engine directly.
"""
