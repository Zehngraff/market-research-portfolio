"""Print the deterministic, offline synthetic demonstration as JSON."""

import json

from .demo import build_demo


def main():
    print(json.dumps(build_demo(), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
