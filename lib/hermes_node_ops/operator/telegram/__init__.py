"""Telegram ingress wedge for the hermes-node operator.

A thin ingress layer that maps Telegram messages to the existing operator
service. Reuses backend, tools, sessions, and constitution unchanged.
Not a parallel agent stack.
"""

__all__: list[str] = []
