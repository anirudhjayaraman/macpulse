"""macpulse was renamed to macverdict. This package only installs macverdict."""

import sys


def main() -> int:
    print("macpulse is now called macverdict. Running `macverdict` for you; use that name from now on.\n",
          file=sys.stderr)
    from macverdict.cli import main as macverdict_main
    return macverdict_main(sys.argv[1:])
