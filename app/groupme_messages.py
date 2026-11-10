"""The wording of the GroupMe bots, read once at startup from the YAML files in app/messages/.

lessons/<key>.yaml is a lesson voice and birthdays/<key>.yaml a birthday style. The file name is the
key a ward has saved, so a new file is a new choice and a deleted one falls back to the default. A
file that can't be used stops the app at startup with its name and the problem, rather than a ward
quietly losing its voice.
"""
from collections.abc import Callable
from pathlib import Path

import yaml

MESSAGES = Path(__file__).parent / "messages"
DEFAULT_VOICE = "plain"       # also the wording around a ward's write-your-own lines
RESERVED = {"mix", "custom"}  # choices on the settings forms that aren't files
LAST = 10_000                 # where a file with no `order` goes

VOICE_LISTS = ("openers", "headers", "teacher", "no_teacher", "no_talk", "nothing", "signoffs")
NO_LESSON = ("fifth_sunday", "stake_conference", "no_second_hour", "conference")


class MessageFileError(Exception):
    pass


def _check_lines(value, path: Path, where: str) -> None:
    if not (isinstance(value, list) and value and all(isinstance(line, str) and line.strip() for line in value)):
        raise MessageFileError(f'{path}: "{where}" must be a list of lines of text, each in double quotes.')


def _check_voice(data: dict, path: Path) -> None:
    for part in VOICE_LISTS:
        _check_lines(data.get(part), path, part)
    no_lesson = data.get("no_lesson")
    if not isinstance(no_lesson, dict):
        raise MessageFileError(f'{path}: "no_lesson" is missing.')
    for reason in NO_LESSON:
        _check_lines(no_lesson.get(reason), path, f"no_lesson: {reason}")


def _check_style(data: dict, path: Path) -> None:
    _check_lines(data.get("messages"), path, "messages")


def _load(folder: Path, check: Callable[[dict, Path], None]) -> dict[str, dict]:
    """Every file in `folder` by file name, in `order` and then alphabetically."""
    found = []
    for path in folder.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise MessageFileError(f"{path}: not valid YAML. {exc}") from None
        if path.stem in RESERVED:
            raise MessageFileError(f'{path}: "{path.stem}" is a built-in choice, so name the file something else.')
        if not (isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"].strip()):
            raise MessageFileError(f'{path}: needs a "name".')
        order = data.pop("order", LAST)
        if not isinstance(order, int):
            raise MessageFileError(f'{path}: "order" must be a whole number.')
        check(data, path)
        found.append((order, path.stem, data))
    return {key: data for _, key, data in sorted(found, key=lambda f: f[:2])}


def load_voices(root: Path = MESSAGES) -> dict[str, dict]:
    voices = _load(root / "lessons", _check_voice)
    if DEFAULT_VOICE not in voices:
        raise MessageFileError(f"{root / 'lessons'}: {DEFAULT_VOICE}.yaml is required.")
    return voices


def load_styles(root: Path = MESSAGES) -> dict[str, tuple[str, list[str]]]:
    """Birthday styles as key: (name, messages)."""
    styles = _load(root / "birthdays", _check_style)
    if not styles:
        raise MessageFileError(f"{root / 'birthdays'}: needs at least one style.")
    return {key: (data["name"], data["messages"]) for key, data in styles.items()}


VOICES = load_voices()
STYLES = load_styles()
