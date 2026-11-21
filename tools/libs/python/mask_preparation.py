"""Helpers shared by the images and Panoramax mask preparation entry points."""

import json


def without_device(settings):
    # The device selects where SAM runs, not which masks are produced.
    if not settings:
        return settings
    return {key: value for key, value in settings.items() if key != "device"}


def read_report(path):
    """Return the parsed JSON object at path, or None when the file is absent."""
    if not path.is_file():
        return None
    report = json.loads(path.read_text())
    if not isinstance(report, dict):
        raise ValueError(f"Rapport invalide : {path}")
    return report


def write_json(path, data):
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)
