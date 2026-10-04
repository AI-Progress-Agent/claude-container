"""The folders that plugin marketplaces are read from on the Mac."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import cast

from launcher.draft import Draft
from launcher.paths import is_under_any


def collect_marketplaces(
    draft: Draft,
    home: str,
    skip_marketplaces: list[str],
    compose_binds: Callable[[str], list[str]],
) -> None:
    """Mounts read-only, at its own path, each folder that a plugin marketplace is read from.

    Claude inside drops a marketplace's plugins when it cannot read the
    folder. folder_marketplaces finds them.

    A marketplace named in skip_marketplaces stays out, such as one that
    belongs to another client. So does a folder the container already sees.
    That is a folder at or under the repo or a sibling repo. It is also one
    at or under a bind mount in any compose file, compose.local.yaml among
    them. compose_binds gives those. A second mount there adds nothing. Under
    a writable mount, it would turn the folder read-only.
    """
    known = f"{home}/.claude/plugins/known_marketplaces.json"
    settings = f"{home}/.claude/settings.json"
    if not os.path.isfile(known) and not os.path.isfile(settings):
        return
    entries = folder_marketplaces(known, settings)
    if entries is None:
        draft.messages.append(
            f"docker/cc: {known} or {settings} does not read as JSON, "
            "so no folder marketplace mounted"
        )
        return
    visible = [draft.repo, *(m.target for m in draft.mounts)]
    compose_read = False
    mounted: list[str] = []
    skipped: list[str] = []
    for name, path in entries:
        if name in skip_marketplaces:
            skipped.append(name)
            continue
        if not os.path.isdir(path):
            draft.messages.append(
                f"docker/cc: marketplace {name} has no folder at {path} on the Mac, "
                "so its plugins will not load"
            )
            continue
        # Only a marketplace to mount needs the compose files read.
        if not compose_read:
            compose_read = True
            visible += compose_binds("a marketplace that a compose file mounts may mount twice")
        if is_under_any(path, visible):
            continue
        draft.mount(path, read_only=True)
        visible.append(path)
        mounted.append(name)
    if mounted:
        draft.messages.append(f"docker/cc: mounted marketplaces read-only: {' '.join(mounted)}")
    if skipped:
        draft.messages.append(f"docker/cc: skipped marketplaces: {' '.join(skipped)}")


def folder_marketplaces(known: str, settings: str) -> list[tuple[str, str]] | None:
    """Each folder marketplace's name and path, or None when a file does not read as JSON.

    Either file may be missing. known_marketplaces.json lists every
    marketplace, from settings.json and from /plugin marketplace add alike.
    Those with a source of "directory" are the folders. The file can lag
    behind settings.json, such as on a new machine. So an entry under
    extraKnownMarketplaces in settings.json wins when the file's entry
    differs or is missing.

    Sorted by path, so a folder inside another marketplace's folder comes
    after it, and counts as visible.
    """
    found: list[tuple[str, str]] = []
    try:
        marketplaces = dict(_json_object(_read_json(known)))
        extra = _json_object(_json_object(_read_json(settings)).get("extraKnownMarketplaces"))
        for name, entry in extra.items():
            if _source(marketplaces.get(name)) != _source(entry):
                marketplaces[name] = entry
        for name, entry in marketplaces.items():
            source = _json_object(_source(entry))
            if source.get("source") != "directory":
                continue
            path = _json_object(entry).get("installLocation") or source.get("path")
            found.append((name, path if isinstance(path, str) else json.dumps(path)))
    except OSError, ValueError:
        return None
    return sorted(found, key=lambda e: e[1])


def _read_json(path: str) -> object:
    """The file's JSON, or None when the file is missing or holds only whitespace.

    jq read an empty file as nothing, as the bash launcher's check did.
    """
    try:
        with open(path, encoding="utf-8") as file:
            text = file.read()
    except FileNotFoundError:
        return None
    return cast(object, json.loads(text)) if text.strip() else None


def _json_object(value: object) -> dict[str, object]:
    """value as a JSON object. null counts as an empty one, and anything else is an error."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return cast(dict[str, object], value)


def _source(entry: object) -> object:
    return _json_object(entry).get("source")
