"""Read controller buttons through XInput, for press-to-assign in the launcher.

The game reads controllers through SDL, which the launcher does not have: the
runtime links it statically. XInput ships with every Windows 10/11 install and
reports buttons by the same positional, Xbox-layout names SDL uses (``a`` is
the bottom face button), so what it captures is exactly what the runtime will
match. Controllers XInput cannot see - a PlayStation pad without Steam Input or
DS4Windows, for one - still work in the game; they are assigned from the list.

Everything returns "nothing pressed" off Windows or without an XInput DLL.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_BUTTONS = (
    # (XINPUT_GAMEPAD_* bit, SDL source name) - order decides which of two
    # buttons pressed in the same poll is taken.
    (0x1000, "a"), (0x2000, "b"), (0x4000, "x"), (0x8000, "y"),
    (0x0001, "dpup"), (0x0002, "dpdown"), (0x0004, "dpleft"), (0x0008, "dpright"),
    (0x0100, "leftshoulder"), (0x0200, "rightshoulder"),
    (0x0040, "leftstick"), (0x0080, "rightstick"),
    (0x0010, "start"), (0x0020, "back"),
)
TRIGGER_PRESSED = 128   # of 255
TRIGGER_RELEASED = 64


class _Gamepad(ctypes.Structure):
    _fields_ = [("wButtons", wintypes.WORD), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _Gamepad)]


_get_state = None
if sys.platform == "win32":
    for _name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            _dll = ctypes.WinDLL(_name)
        except OSError:
            continue
        _get_state = _dll.XInputGetState
        _get_state.argtypes = [wintypes.DWORD, ctypes.POINTER(_State)]
        _get_state.restype = wintypes.DWORD
        break


def available() -> bool:
    return _get_state is not None


def snapshot() -> dict[int, tuple[int, int, int]]:
    """{user index: (buttons, left trigger, right trigger)} for connected pads."""
    out = {}
    if _get_state is None:
        return out
    state = _State()
    for user in range(4):
        if _get_state(user, ctypes.byref(state)) == 0:
            pad = state.Gamepad
            out[user] = (pad.wButtons, pad.bLeftTrigger, pad.bRightTrigger)
    return out


class PressWatcher:
    """Report the first button pressed AFTER it started watching.

    Anything already held when watching starts - the mouse-click that opened
    the dialog could not have come from a pad, but a thumb resting on a
    trigger could - only counts once it has been released and pressed again.
    """

    def __init__(self):
        self._held = {user: (buttons, lt >= TRIGGER_RELEASED, rt >= TRIGGER_RELEASED)
                      for user, (buttons, lt, rt) in snapshot().items()}

    def connected(self) -> int:
        return len(snapshot())

    def poll(self) -> str | None:
        for user, (buttons, lt, rt) in snapshot().items():
            held_buttons, held_lt, held_rt = self._held.get(user, (0, False, False))
            fresh = buttons & ~held_buttons
            for bit, name in _BUTTONS:
                if fresh & bit:
                    return name
            if lt >= TRIGGER_PRESSED and not held_lt:
                return "lefttrigger"
            if rt >= TRIGGER_PRESSED and not held_rt:
                return "righttrigger"
            # Releases re-arm a button that was held when watching began.
            self._held[user] = (held_buttons & buttons,
                                held_lt and lt >= TRIGGER_RELEASED,
                                held_rt and rt >= TRIGGER_RELEASED)
        return None
