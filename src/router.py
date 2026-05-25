from src.config import ModelEntry


class ModelRouter:
    def __init__(self, models: list[ModelEntry]):
        self._models = models

    def match(self, model_name: str, provider: str) -> tuple[ModelEntry, str] | None:
        """Find a ModelEntry by model name, checking it supports the given provider endpoint type.

        Returns (entry, backend_model_name) or None.
        backend_model_name is the name to send to the backend (may differ from client model_name).
        """
        for entry in self._models:
            name_map = entry.get_name_map()
            if model_name in name_map:
                if provider == "openai" and entry.openai_base_url:
                    return (entry, name_map[model_name])
                if provider == "anthropic" and entry.anthropic_base_url:
                    return (entry, name_map[model_name])
                return None
        return None

    def list_models(self, provider: str) -> list[str]:
        """List all client-facing model names available for a given provider endpoint type."""
        result = []
        for entry in self._models:
            if provider == "openai" and entry.openai_base_url:
                result.extend(entry.client_names)
            elif provider == "anthropic" and entry.anthropic_base_url:
                result.extend(entry.client_names)
        return result
