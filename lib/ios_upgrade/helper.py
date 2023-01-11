#!/usr/bin/env python3
import sys
import os
import re
import argparse
from pathlib import Path
from subprocess import run, CompletedProcess
from stat import S_ISREG
from typing import Optional, List, Dict, Union, NamedTuple

import colorama		# type: ignore

CONFIG_DIR  = Path(sys.argv[0]).resolve().parents[1] / "etc" / "ios-upgrade"
# DEVICE_APPLY = "/opt/Shell/tooling/device-apply/bin/device-apply"
DEVICE_APPLY = "device-apply"
# Setting DEVICE_APPLY_NAME currently doesn't do anything since python fakes
# sys. argv. In python 3.10 a new variable sys.orig_argv will be added
# DEVICE_APPLY_NAME = "device-apply"
DEVICE_APPLY_NAME = "ios-upgrade"
PACKAGE_VERSION = "1.0"
PREFIX = "IOS-upgrade."
VERSION_MIN = "1.6.13"
IOS_TABLE_OPTION = "--ios-table"
IOS_TABLE = "cisco_ios.table.txt"
ENV_IOS_TABLE    = "ios_table"
ENV_ACTIONS_NAME = "actions_name"

DROP_OPTIONS = set("""
--actions-file
--execute
--udiff
--device-type
--globals
--auto-if
--ignore-error
--version
--help
""".split())

# class MyParser(ArgumentParser):

class Option(NamedTuple):
    long:	str
    help:	str
    actions_name: Optional[str] = None
    exec: Optional[bool] = None

    def no(self):
        return "--no-" + self.long[2:]

class Args(NamedTuple):
    parsed_args: Dict[str, Union[str, bool]]
    extra_args: List[str]
    extra_options: Dict[str, Optional[bool]]
    actions_name: Optional[str]
    exec: bool

    def argv(self) -> List[str]:
        argv = []
        for option, value in self.parsed_args.items():
            if value is True:
                argv.append(option)
            elif value is False:
                if not option.startswith("--"):
                    raise AssertionError(f"Cannot set {option} to False")
                argv.append("--no-" + option[2:])
            elif isinstance(value, str):
                argv += [option, value]
            else:
                raise AssertionError(f"Unhandled type. {option=}: {value=}")
        return argv + self.extra_args

    def setdefault(self, option: str, value: Union[str, bool, Path]):
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            self.parsed_args.setdefault(option, value)
        elif isinstance(value, bool):
            if option.startswith("--"):
                if option not in self.parsed_args and \
                   "--no-" + option[2:] not in self.parsed_args:
                    if value:
                        self.parsed_args[option] = True
                    else:
                        self.parsed_args["--no-" + option[2:]] = True
            else:
                self.parsed_args.setdefault(option, value)
        else:
            raise AssertionError(f"Unhandled type. {option=}: {value=}")

def red(text: str) -> str:
    """Wrap text in ANSI escapes so it will display as red

    Args:
        text: The text to be decorated as red

    Returns:
        Decorated text
    """
    result: str = colorama.Style.BRIGHT + colorama.Fore.RED + text + colorama.Fore.RESET + colorama.Style.NORMAL
    return result

def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        usage = "%(prog)s [options] [--] [<device>...]",
        add_help = False,
        allow_abbrev = False,
        argument_default=argparse.SUPPRESS)
    parser.add_argument(
        IOS_TABLE_OPTION,
        default = IOS_TABLE,
        metavar = "FILE",
        dest = IOS_TABLE_OPTION,
        help = f"IOS table file (default {IOS_TABLE!r})")
    return parser

def parse_args(options: Optional[List[Option]]) -> Args:
    result = run([DEVICE_APPLY_NAME, "--help", "--actions"],
                 executable = DEVICE_APPLY,
                 text=True,
                 capture_output=True)
    if result.returncode:
        print(red(result.stderr.strip() or result.stdout.strip() or "Unknown error"))
        sys.exit(result.returncode if result.returncode > 0 else 1)
    parts = re.split(r"(^[^\S\n]*Usage:[^\S\n]*(?:\n[^\S\n]*)*$)",
                     result.stdout.expandtabs(),
                     maxsplit = 2,
                     flags = re.IGNORECASE | re.MULTILINE)
    if len(parts) < 3:
        raise AssertionError(f"Missing Usage: section in {DEVICE_APPLY} --help")
    if len(parts) > 3:
        raise AssertionError("Multiple Usage: sections in {DEVICE_APPLY} --help")
    match = re.search(r"[^\S\n]*\n[^\S\n]*\n(?=\S|\Z)|\Z\n?", parts[2])
    if not match:
        raise AssertionError(r"Must always match \Z")

    post_usage = parts[2][match.end():]
    parts = ["Options:"] + re.split(r"^[^\S\n]*(.*?(?:(?:Sub)?commands?|Directives?|Options?):)[^\S\n]*",
                                    post_usage,
                                    flags = re.IGNORECASE | re.MULTILINE)

    parser = argument_parser()
    if options is None:
        options = []

    for extra_option in options:
        parser.add_argument(
            extra_option.long,
            action = "store_true",
            dest = extra_option.long,
            help = extra_option.help)
        parser.add_argument(
            extra_option.no(),
            action = "store_true",
            dest = extra_option.no(),
            help = argparse.SUPPRESS)

    for header, body in zip(parts[::2], parts[1::2]):
        # Skip the initial newline
        body = body[1:]
        if not body:
            # This body contributes nothing. Skip it.
            # This is often caused by the extra "Body:" we just put in front
            continue

        # Look for option declarations in the section body
        # These are lines that start with `<IDENTIFIER>` or `-`
        split = re.split(r"^[^\S\n]*(<\S+?>|-\S+)", body,
                         flags = re.MULTILINE)

        for s1, s2 in zip(split[1::2], split[2::2]):
            if not s1.startswith("-"):
                # Ok, so for the moment we don't actually handle <IDENTIFIER>
                continue
            option = (s1 + s2).strip()
            if not option.startswith("-"):
                raise AssertionError("Option does not start with '-': %s" % option)
            # Description starts after 2 white spaces
            match = re.match(r".*?(?:[^\S\n]{2}|$)", option,
                             flags = re.MULTILINE)
            if match:
                description = option[match.end():].partition("\n\n")[0]
                option = match[0]
            else:
                description = ""
            # At this point option is something like "-C --credentials FILE"
            parts = option.split()
            if any(part in DROP_OPTIONS for part in parts):
                continue
            if parts[-1].startswith("-"):
                argument = ""
            else:
                argument = parts.pop()
            dest = next((part for part in parts if part.startswith("--")), parts[0])
            if argument:
                parser.add_argument(*parts,
                                    metavar = argument,
                                    dest = dest,
                                    help = description)
            else:
                parser.add_argument(*parts,
                                    action = "store_true",
                                    dest = dest,
                                    help = description)
                for part in parts:
                    if part.startswith("--"):
                        parser.add_argument("--no-" + part[2:],
                                            action = "store_true",
                                            dest = "--no-" + part[2:],
                                            help=argparse.SUPPRESS)

    parser.add_argument(
        "--version",
        action="version",
        version=PACKAGE_VERSION)
    parser.add_argument(
        "-h", "--help",
        action="help",
        help="show this help message and exit")

    argv = sys.argv[1:]
    # Stop parsing at "--"
    try:
        index = argv.index("--")
        extra_args = argv[index+1:]
        argv = argv[:index]
    except ValueError:
        extra_args = []

    parsed, more_args = parser.parse_known_args(argv)
    bad_args = [arg for arg in more_args if arg.startswith("-")]
    if bad_args:
        parser.error("Unknown option %s" %
                     ", ".join(dict.fromkeys(bad_args)))

    parsed_args: Dict[str, Union[str, bool]] = vars(parsed)
    extra_options: Dict[str, Optional[bool]] = {}
    actions_name: Optional[str] = None
    exec: bool = True
    for extra_option in options:
        value = parsed_args.pop(extra_option.long, None)
        assert not isinstance(value, str)
        extra_options[extra_option.long] = value
        if extra_options[extra_option.long]:
            if extra_option.no() in parsed_args:
                parser.error(f"Cannot have both {extra_option.long!r} and {extra_option.no()!r}")
            if extra_option.actions_name is not None:
                actions_name = extra_option.actions_name
            if extra_option.exec is not None:
                exec = extra_option.exec
        elif parsed_args.pop(extra_option.no(), None):
            extra_options[extra_option.long] = False

    return Args(
        parsed_args = parsed_args,
        extra_args = ["--"] + more_args + extra_args,
        extra_options = extra_options,
        actions_name = actions_name,
        exec = exec
    )

def helper(*,
           extra_options: Optional[List[Option]] = None,
           name: Optional[str] = None) -> Args:
    args = parse_args(extra_options)

    if name is None:
        name = args.actions_name
    if name is None:
        name = Path(sys.argv[0]).name
        if not name.startswith(PREFIX):
            raise SystemExit(red(f"Assertion: Wrapper script name {name!r} does not start with {PREFIX!r}"))
        name = name[len(PREFIX):]

    if name == "check":
        args.setdefault("--work-dir", "-")
    if args.parsed_args.get("--work-dir", ".") == "":
        del args.parsed_args["--work-dir"]
    args.setdefault("--verbose", True)
    args.setdefault("--check-alone", False)
    args.parsed_args["--actions-file"] = str(CONFIG_DIR / ("%s.actions.txt" % name))
    ios_table = args.parsed_args.pop(IOS_TABLE_OPTION)
    assert isinstance(ios_table, str)

    # Make sure IOS_TABLE is usable. These tests are a race condition since the
    # situation can have changed by the time of the actual usage, but that does
    # not matter since that usage will still error in case of problems, it's
    # just that these error will be a lot less clear
    ios_table_path = Path(ios_table).resolve()
    try:
        stat = ios_table_path.stat()
    except IOError as exc:
        raise SystemExit(red(str(exc).replace(str(ios_table_path), IOS_TABLE))) from None
    if not S_ISREG(stat.st_mode):
        raise SystemExit(red(f"Path {IOS_TABLE!r} exists but is not a regular file"))
    try:
        with open(ios_table_path):
            pass
    except IOError as exc:
        raise SystemExit(red(str(exc).replace(str(ios_table_path), IOS_TABLE))) from None
    os.environ[ENV_IOS_TABLE] = str(ios_table_path)

    # Check cisco IOS table
    result = run([DEVICE_APPLY_NAME,
                  "--version-min", VERSION_MIN,
                  "--no-verbose",
                  "-W", "-",
                  "-A", str(CONFIG_DIR / "IOS.check_table.actions.txt"),
                  os.devnull],
                 executable = DEVICE_APPLY,
                 text=True,
                 capture_output=True)
    if result.returncode:
        print(red(result.stderr.strip() or result.stdout.strip() or "Unknown error"))
        sys.exit(result.returncode if result.returncode > 0 else 1)

    if args.exec:
        program = [DEVICE_APPLY_NAME] + args.argv()
        os.execvp(DEVICE_APPLY, program)
    return args

def run_args(args: Args) -> CompletedProcess:
    program = [DEVICE_APPLY_NAME] + args.argv()
    return run(program, executable = DEVICE_APPLY)
