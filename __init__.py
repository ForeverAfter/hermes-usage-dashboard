"""Usage Dashboard agent plugin.

The feature is exposed through its dashboard FastAPI route and desktop UI.
No agent hooks or tools are registered.
"""


def register(ctx):
    """Keep the Python plugin side intentionally inert."""
    return None
