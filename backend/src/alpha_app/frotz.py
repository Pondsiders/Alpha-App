"""frotz — cause something to give off light.

Auto-generated CLI from Alpha-App's OpenAPI spec. On startup, frotz
fetches /openapi.json from the running app and dynamically builds
Click commands from every endpoint. Adding a FastAPI route automatically
creates a frotz command. The spell book writes itself.

Named after the Enchanter spell: "cause something to give off light."
From the Frobozz Magic Tool Company. Keep the lights on.
"""

import json
import sys

import click
import httpx

from alpha_app.constants import PORT

BASE = f"http://localhost:{PORT}"
CLIENT = httpx.Client(base_url=BASE, timeout=30)


def _fetch_spec() -> dict:
    """Fetch the OpenAPI spec from the running app."""
    try:
        return CLIENT.get("/openapi.json").json()
    except httpx.ConnectError:
        click.echo("Alpha is not running.", err=True)
        sys.exit(1)


def _call(method: str, path: str, path_params: dict, body: dict | None) -> None:
    """Execute an HTTP request and pretty-print the response."""
    for name, value in path_params.items():
        path = path.replace(f"{{{name}}}", str(value))

    try:
        r = CLIENT.request(method, path, json=body)
        r.raise_for_status()
        data = r.json()
        if data is None:
            click.echo("(no data)")
        elif isinstance(data, list) and not data:
            click.echo("(empty)")
        else:
            click.echo(json.dumps(data, indent=2, default=str))
    except httpx.HTTPStatusError as e:
        click.echo(f"Error {e.response.status_code}: {e.response.text}", err=True)
        sys.exit(1)
    except httpx.ConnectError:
        click.echo("Alpha is not running.", err=True)
        sys.exit(1)


def _resolve_ref(schema: dict, spec: dict) -> dict:
    """Resolve a $ref or anyOf schema to its concrete definition."""
    if "$ref" in schema:
        parts = schema["$ref"].split("/")[1:]  # skip "#"
        node = spec
        for part in parts:
            node = node[part]
        return node
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            if option.get("type") != "null":
                return _resolve_ref(option, spec)
    return schema


def _build_cli(spec: dict) -> click.Group:
    """Build the entire CLI dynamically from an OpenAPI spec."""
    groups: dict[str, click.Group] = {}
    root = click.Group(name="frotz", help="frotz — cause something to give off light. 🔥")

    skip = {"/{full_path}", "/health"}
    skip_prefix = ("/api/demo", "/api/theme", "/api/threads")

    for path, methods in spec.get("paths", {}).items():
        if path in skip or any(path.startswith(p) for p in skip_prefix):
            continue

        for method, op in methods.items():
            parts = path.strip("/").split("/")
            if len(parts) < 2 or parts[0] != "api":
                continue

            group_name = parts[1]
            if group_name not in groups:
                groups[group_name] = click.Group(name=group_name)
                root.add_command(groups[group_name])

            # Derive command name from path tail + method
            tail = [p for p in parts[2:] if not p.startswith("{")]
            if tail:
                cmd_name = "-".join(tail)
            else:
                cmd_name = {"get": "show", "post": "create", "delete": "clear"}.get(method, method)

            # Deduplicate
            existing = {c.name for c in groups[group_name].commands.values()}
            if cmd_name in existing:
                cmd_name = f"{cmd_name}-{method}"

            # Gather path params
            path_param_names = [
                p["name"] for p in op.get("parameters", []) if p.get("in") == "path"
            ]

            # Gather body schema
            body_schema = None
            rb = op.get("requestBody", {}).get("content", {}).get("application/json", {})
            if "schema" in rb:
                body_schema = _resolve_ref(rb["schema"], spec)

            # Build Click params
            click_params = [click.Argument([n]) for n in path_param_names]
            body_props = {}

            if body_schema and "properties" in body_schema:
                required = set(body_schema.get("required", []))
                for prop, defn in body_schema["properties"].items():
                    ptype = {"string": str, "integer": int, "boolean": bool,
                             "number": float}.get(defn.get("type", "string"), str)
                    default = defn.get("default")

                    if ptype is bool:
                        flag = prop.replace("_", "-")
                        click_params.append(click.Option(
                            [f"--{flag}/--no-{flag}"],
                            default=default if default is not None else False,
                        ))
                    elif prop in required:
                        click_params.append(click.Argument([prop], type=ptype))
                    else:
                        click_params.append(click.Option(
                            [f"--{prop.replace('_', '-')}"], type=ptype, default=default,
                        ))
                    body_props[prop] = defn

            # Create command with a closure
            def _make_cb(m, p, pp, bp):
                def cb(**kw):
                    pv = {n: kw.pop(n) for n in pp}
                    b = {k: kw[k.replace("-", "_")] for k in bp if kw.get(k.replace("-", "_")) is not None}
                    _call(m.upper(), p, pv, b or None)
                return cb

            groups[group_name].add_command(click.Command(
                name=cmd_name,
                help=op.get("summary", ""),
                params=click_params,
                callback=_make_cb(method, path, path_param_names, body_props),
            ))

    # Health as a top-level command (always available)
    @root.command("health")
    def health():
        """Check if Alpha is running."""
        _call("GET", "/health", {}, None)

    return root


class FrotzCLI(click.MultiCommand):
    """A Click MultiCommand that lazily builds itself from the OpenAPI spec."""

    def __init__(self):
        super().__init__(name="frotz", help="frotz — cause something to give off light. 🔥")
        self._inner = None

    def _ensure_built(self):
        if self._inner is None:
            self._inner = _build_cli(_fetch_spec())

    def list_commands(self, ctx):
        self._ensure_built()
        return sorted(self._inner.commands.keys())

    def get_command(self, ctx, cmd_name):
        self._ensure_built()
        return self._inner.commands.get(cmd_name)


def main():
    FrotzCLI()(standalone_mode=True)


if __name__ == "__main__":
    main()
