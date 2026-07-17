"""One-shot fix for the HUD kiosk's stuck mouse cursor.

There is no physical pointer device on this Pi. Chromium (running under
cage, a minimal Wayland compositor) can only honor the HUD page's
`cursor: none` CSS request after it has received at least one real
mouse-enter event with a Wayland input serial — without any pointer device
ever generating one, that request just sits unresolved forever and
Chromium logs "Failed to hide cursor. No mouse enter serial found.",
showing its default cursor sprite motionless on screen since nothing ever
moves it.

This creates a temporary virtual mouse via uinput and nudges it by one
pixel and back, which is enough to generate that one enter event, then
exits — the cursor should then honor the page's existing `cursor: none`
and stay hidden, since nothing else will ever move it again.
"""
import time

from evdev import UInput, ecodes as e

CAPABILITIES = {
    e.EV_REL: [e.REL_X, e.REL_Y],
    e.EV_KEY: [e.BTN_LEFT],
}

# Give cage and Chromium time to finish starting and load the page before
# the nudge — too early and there's no surface yet to receive the event.
STARTUP_DELAY_SECONDS = 4


def main():
    time.sleep(STARTUP_DELAY_SECONDS)

    with UInput(CAPABILITIES, name="atlas-virtual-mouse") as ui:
        ui.write(e.EV_REL, e.REL_X, 1)
        ui.write(e.EV_REL, e.REL_Y, 1)
        ui.syn()
        time.sleep(0.1)
        ui.write(e.EV_REL, e.REL_X, -1)
        ui.write(e.EV_REL, e.REL_Y, -1)
        ui.syn()
        time.sleep(1)


if __name__ == "__main__":
    main()
