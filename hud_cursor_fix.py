"""Fix for the HUD kiosk's stuck mouse cursor.

There is no physical pointer device on this Pi. Chromium (running under
cage, a minimal Wayland compositor) can only honor the HUD page's
`cursor: none` CSS request after it has received at least one real
mouse-enter event with a Wayland input serial — without any pointer device
ever generating one, that request just sits unresolved forever and
Chromium logs "Failed to hide cursor. No mouse enter serial found.",
showing its default cursor sprite motionless on screen since nothing ever
moves it.

This creates a temporary virtual mouse via uinput and nudges it by one
pixel and back, which is enough to generate that one enter event. A single
nudge at a fixed delay turned out unreliable — Chromium's actual startup
time (surface creation, page load) varies enough between runs that a nudge
timed too early lands before there's anything to receive it, and the
cursor stays stuck. Nudging several times over a longer window makes this
robust to that variance instead of depending on one lucky guess.
"""
import time

from evdev import UInput, ecodes as e

CAPABILITIES = {
    e.EV_REL: [e.REL_X, e.REL_Y],
    e.EV_KEY: [e.BTN_LEFT],
}

# Spread across a wide window rather than one fixed guess — cage/Chromium
# startup time varies enough between runs that a single-attempt delay
# sometimes lands before there's a surface ready to receive the nudge.
NUDGE_DELAYS_SECONDS = [3, 6, 10, 15, 20]


def nudge(ui):
    ui.write(e.EV_REL, e.REL_X, 1)
    ui.write(e.EV_REL, e.REL_Y, 1)
    ui.syn()
    time.sleep(0.1)
    ui.write(e.EV_REL, e.REL_X, -1)
    ui.write(e.EV_REL, e.REL_Y, -1)
    ui.syn()


def main():
    with UInput(CAPABILITIES, name="atlas-virtual-mouse") as ui:
        elapsed = 0.0

        for delay in NUDGE_DELAYS_SECONDS:
            time.sleep(delay - elapsed)
            elapsed = delay
            nudge(ui)

        time.sleep(1)


if __name__ == "__main__":
    main()
