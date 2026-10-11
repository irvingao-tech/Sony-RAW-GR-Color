import argparse
import json
from pathlib import Path
import sys

import rawpy

from . import __version__
from .export import export_neutral
from .raw import ProcessingError, decode
from .develop import export_developed
from .edits import Edits, load_sidecar, save_sidecar


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="S2G: Sony A7C II RAW editor")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report decoder runtime versions")
    inspect = sub.add_parser("inspect", help="Decode ARW and report metadata without exporting")
    inspect.add_argument("source", type=Path)
    render = sub.add_parser("render", help="Export a neutral linear 16-bit TIFF and JSON manifest")
    render.add_argument("source", type=Path)
    render.add_argument("--output", "-o", type=Path, required=True)
    render.add_argument("--half-size", action="store_true", help="Use a half-resolution decode")
    develop = sub.add_parser("develop", help="Export edited JPEG or 16-bit sRGB TIFF")
    develop.add_argument("source", type=Path)
    develop.add_argument("--output", "-o", type=Path, required=True)
    develop.add_argument("--max-size", type=int)
    for command in (develop, sub.add_parser("edit", help="Save edits beside the RAW")):
        if command is not develop:
            command.add_argument("source", type=Path)
        from .edits import LOOK_IDS
        command.add_argument("--look", choices=LOOK_IDS)
        command.add_argument("--exposure", type=float)
        command.add_argument("--temperature", type=float)
        command.add_argument("--strength", type=float)
        command.add_argument("--grain", type=float)
        command.add_argument("--saturation", type=float)
        command.add_argument("--vibrance", type=float)
        command.add_argument("--split-toning", type=float)
    serve = sub.add_parser("serve", help="Start the offline local editor")
    serve.add_argument("--port", type=int, default=8876)
    serve.add_argument("--open", action="store_true", help="Open the editor in your browser")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            result = {"s2g": __version__, "rawpy": rawpy.__version__, "libraw": list(rawpy.libraw_version)}
        elif args.command == "inspect":
            _, result = decode(args.source, pixels=False)
        elif args.command == "render":
            result = export_neutral(args.source, args.output, half_size=args.half_size)
        elif args.command in ("edit", "develop"):
            values = load_sidecar(args.source).to_dict()
            for arg, key in (("look", "look"), ("exposure", "exposure_ev"), ("temperature", "temperature"), ("strength", "strength"), ("grain", "grain"),
                             ("saturation", "saturation"), ("vibrance", "vibrance"), ("split_toning", "split_toning")):
                if getattr(args, arg) is not None:
                    values[key] = getattr(args, arg)
            edit = Edits.from_dict(values)
            result = save_sidecar(args.source, edit) if args.command == "edit" else export_developed(args.source, args.output, edit, max_size=args.max_size)
        else:
            from .server import run_server
            run_server(args.port, open_browser=args.open)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ProcessingError, OSError, ValueError) as exc:
        print(f"S2G error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
