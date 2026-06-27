import argparse
import importlib.resources
import os
import sys


def _write_default_config(path):
    """Copy default_config.yaml (bundled with the package) to the given path."""
    content = importlib.resources.read_text("src", "default_config.yaml")
    with open(path, "w") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser(prog="two-api", description="Start the two-API LLM proxy")
    parser.add_argument("config", nargs="?", default="~/.two-api/config.yaml",
                        help="Path to config.yaml (default: ~/.two-api/config.yaml)")
    parser.add_argument("--host", default=None, help="Override server host")
    parser.add_argument("--port", type=int, default=None, help="Override server port")
    args = parser.parse_args()

    config_path = os.path.expanduser(args.config)
    if not os.path.exists(config_path):
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        _write_default_config(config_path)
        print(f"Created default config: {config_path}")

    import uvicorn
    from src.config import load_config, ServerConfig

    config = load_config(config_path)
    host = args.host or config.server.host
    port = args.port or config.server.port

    print(f"Starting two-API on http://{host}:{port}")
    print(f"Config: {args.config}")

    # Auto-detect development mode: enable reload when running from project root
    reload = os.path.isdir("src") and os.path.isfile("pyproject.toml")
    uvicorn.run("src.main:app", host=host, port=port, log_level="info", reload=reload)


if __name__ == "__main__":
    main()
