from src.config import ModelEntry


class ModelRouter:
    def __init__(self, models: list[ModelEntry]):
        self._models = models

    def match(self, model_name: str, provider: str) -> ModelEntry | None:
        """Find a ModelEntry by model name, checking it supports the given provider endpoint type.

        Args:
            model_name: The model name from the request body.
            provider: 'openai' or 'anthropic' — which endpoint type the request came in on.

        Returns:
            ModelEntry if found and compatible, None otherwise.
        """
        for entry in self._models:
            if model_name in entry.names:
                if provider == "openai" and entry.openai_base_url:
                    return entry
                if provider == "anthropic" and entry.anthropic_base_url:
                    return entry
                return None
        return None

    def list_models(self, provider: str) -> list[str]:
        """List all model names available for a given provider endpoint type."""
        result = []
        for entry in self._models:
            if provider == "openai" and entry.openai_base_url:
                result.extend(entry.names)
            elif provider == "anthropic" and entry.anthropic_base_url:
                result.extend(entry.names)
        return result
