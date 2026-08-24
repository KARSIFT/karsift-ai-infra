#!/usr/bin/env python3
"""Validate and prepare a stored Cursor binding for ``agent --model``.

``roles.yml`` stores provider-qualified bindings while Cursor CLI expects the
bare model expression. Parameter overrides are part of that expression and
must survive byte-for-byte after the provider prefix is removed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys


CURSOR_PREFIX = "cursor/"
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PARAMETER_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*=[A-Za-z0-9][A-Za-z0-9._-]*$"
)


class CursorModelError(ValueError):
    """Fail-closed model-routing refusal with a safe reason code."""


def _validate_cli_model(cli_model: str) -> None:
    if not cli_model:
        raise CursorModelError("empty_cursor_model")

    if "[" not in cli_model and "]" not in cli_model:
        if not MODEL_RE.fullmatch(cli_model):
            raise CursorModelError("invalid_cursor_model")
        return

    if cli_model.count("[") != 1 or cli_model.count("]") != 1:
        raise CursorModelError("invalid_cursor_model_parameters")
    if not cli_model.endswith("]"):
        raise CursorModelError("invalid_cursor_model_parameters")

    model, parameters = cli_model[:-1].split("[", 1)
    if not MODEL_RE.fullmatch(model) or not parameters:
        raise CursorModelError("invalid_cursor_model_parameters")

    seen: set[str] = set()
    for parameter in parameters.split(","):
        if not PARAMETER_RE.fullmatch(parameter):
            raise CursorModelError("invalid_cursor_model_parameters")
        key = parameter.split("=", 1)[0]
        if key in seen:
            raise CursorModelError("duplicate_cursor_model_parameter")
        seen.add(key)


def prepare_cursor_model(stored: str, *, require_api_key: bool = False) -> str:
    if require_api_key and not os.environ.get("CURSOR_API_KEY"):
        raise CursorModelError("missing_cursor_api_key")
    if not stored.startswith(CURSOR_PREFIX):
        raise CursorModelError("unsupported_provider_prefix")

    cli_model = stored[len(CURSOR_PREFIX) :]
    _validate_cli_model(cli_model)
    return cli_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stored_model",
        help="Provider-qualified model from roles.yml",
    )
    parser.add_argument(
        "--require-api-key",
        action="store_true",
        help="Fail closed when CURSOR_API_KEY is unset; never print its value.",
    )
    args = parser.parse_args(argv)

    try:
        cli_model = prepare_cursor_model(
            args.stored_model,
            require_api_key=args.require_api_key,
        )
    except CursorModelError as exc:
        print(f"prepare-cursor-model: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(cli_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
