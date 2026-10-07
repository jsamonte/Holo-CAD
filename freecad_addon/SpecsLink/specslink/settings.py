"""Preferences, kept in FreeCAD's own parameter store.

Using ParamGet means the settings live where every other FreeCAD preference
lives, survive restarts without a config file of our own, and need no setup
step from anyone installing the addon.
"""

from __future__ import annotations

import FreeCAD

GROUP = "User parameter:BaseApp/Preferences/Mod/SpecsLink"

# The relay this build ships against, for example "https://relay.example.com".
#
# Fill this in and every user only has to type the pairing code their glasses
# show. Leave it empty and they would have to enter the address as well,
# which is one more thing to get wrong and one more thing to explain.
#
# Set this to the relay serving the published lens, so the addon and the
# lens agree without anyone configuring anything.
DEFAULT_RELAY_URL = ""

DEFAULTS = {
    "Port": 8765,
    "AutoStart": True,
    "ScaleMode": "true_size",   # true_size, ratio, fit
    "ScaleFactor": 1.0,         # 0.1 means 1:10
    "FitTargetMm": 300.0,       # largest dimension, when ScaleMode is fit
    "Quality": "normal",        # draft, normal, fine
    "PreferStockExporter": False,
    "LiveDebounceMs": 500,
    "PerBody": True,
    # Relay, for a published lens that cannot reach this machine directly.
    # On by default when this build ships with a relay, since a published
    # lens can reach FreeCAD no other way.
    "UseRelay": bool(DEFAULT_RELAY_URL),
    "RelayUrl": DEFAULT_RELAY_URL,
    "PairingCode": "",
}

SCALE_MODES = ("true_size", "ratio", "fit")
QUALITIES = ("draft", "normal", "fine")


def _params():
    return FreeCAD.ParamGet(GROUP)


def get(name):
    """One setting, falling back to the default when unset or nonsense."""
    default = DEFAULTS[name]
    params = _params()
    if isinstance(default, bool):
        return params.GetBool(name, default)
    if isinstance(default, int):
        return params.GetInt(name, default)
    if isinstance(default, float):
        return params.GetFloat(name, default)
    return params.GetString(name, default) or default


def set_value(name, value) -> None:
    default = DEFAULTS[name]
    params = _params()
    if isinstance(default, bool):
        params.SetBool(name, bool(value))
    elif isinstance(default, int):
        params.SetInt(name, int(value))
    elif isinstance(default, float):
        params.SetFloat(name, float(value))
    else:
        params.SetString(name, str(value))


def all_values() -> dict:
    return {name: get(name) for name in DEFAULTS}


def scale_spec() -> dict:
    """The scale block the lens expects, built from the current settings."""
    mode = get("ScaleMode")
    if mode not in SCALE_MODES:
        mode = "true_size"
    return {
        "mode": mode,
        "factor": float(get("ScaleFactor")),
        "target_mm": float(get("FitTargetMm")) if mode == "fit" else None,
    }


def relay_target():
    """(base url, pairing code) when relay sending is switched on, else None."""
    if not get("UseRelay"):
        return None
    base = (get("RelayUrl") or "").strip().rstrip("/")
    code = (get("PairingCode") or "").strip().upper()
    if not base or not code:
        return None
    return base, code


def quality() -> str:
    value = get("Quality")
    return value if value in QUALITIES else "normal"


def describe_scale() -> str:
    """A short phrase for the Report view, so the mode is never a surprise."""
    spec = scale_spec()
    if spec["mode"] == "ratio":
        factor = spec["factor"]
        if factor <= 0:
            return "1:1 (the ratio was not positive)"
        if factor < 1:
            return "1:{0:g}".format(round(1.0 / factor, 2))
        return "{0:g}:1".format(factor)
    if spec["mode"] == "fit":
        return "fit to {0:g} mm".format(spec["target_mm"] or 0)
    return "1:1 true size"
