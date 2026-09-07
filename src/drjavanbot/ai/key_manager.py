from __future__ import annotations

from drjavanbot.secrets import AVALAI_API_KEY_SECRET, SecretStore
from .provider import AvalAIClient


class AvalAIKeyManager:
    """Validate a candidate key before atomically replacing the stored key."""

    def __init__(self, store: SecretStore, client: AvalAIClient) -> None:
        self.store = store
        self.client = client

    def validate_and_store(self, candidate: str) -> bool:
        key = candidate.strip()
        if len(key) < 8 or "\n" in key or "\r" in key or any(ch.isspace() for ch in key):
            return False
        if not self.client.validate_api_key(key):
            return False
        self.store.set_secret(AVALAI_API_KEY_SECRET, key)
        return True

    def remove(self) -> bool:
        return self.store.delete_secret(AVALAI_API_KEY_SECRET)

    def configured(self) -> bool:
        return self.store.is_configured(AVALAI_API_KEY_SECRET)
