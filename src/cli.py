import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="two-api", description="Start the two-API LLM proxy")
    parser.add_argument("config", nargs="?", default="config.yaml",
                        help="Path to config.yaml (default: config.yaml)")
    parser.add_argument("--host", default=None, help="Override server host")
    parser.add_argument("--port", type=int, default=None, help="Override server port")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    import uvicorn
    from src.config import load_config, ServerConfig

    config = load_config(args.config)
    host = args.host or config.server.host
    port = args.port or config.server.port

    print(f"Starting two-API on http://{host}:{port}")
    print(f"Config: {args.config}")
    uvicorn.run("src.main:app", host=host, port=port, log_level="info", reload=True)


if __name__ == "__main__":
    main()
