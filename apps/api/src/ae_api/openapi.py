"""Print the OpenAPI document (packages/api-types generates the web app's TypeScript types from it)."""

from __future__ import annotations

import json
import sys

from .main import create_app
from .settings import Settings


def main() -> None:
    spec = create_app(Settings(app_env="development")).openapi()
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
