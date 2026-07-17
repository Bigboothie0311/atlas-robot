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
cursor stays stuck. A first attempt at spreading several nudges over 20s
still wasn't enough — confirmed on a real cold boot where the service
didn't report "Started" until 22s in, after every one of 5 nudges (at
3/6/10/15/20s) had already fired into a surface that didn't exist yet.
Nudging periodically over a much longer window fixes this properly
instead of guessing at a fixed cutoff — cheap and harmless to keep
trying since a nudge into a not-yet-ready surface is a silent no-op, not
an error.

Runs as atlas-hud.service's ExecStartPost, which means any unhandled
exception here fails the *entire* service start under Type=simple —
Restart=on-failure then kills the already-running cage/Chromium and
retries the whole thing, over what should only ever be a cosmetic nudge.
Confirmed this happening for real on a cold boot: /dev/uinput briefly
doesn't have the group permissions the udev rule grants it (a timing gap
between the uinput module loading and udev applying the rule), UInput()
raised, and the whole kiosk got yanked down and restarted because of it.
Every failure path below is caught and swallowed instead of raised, and
UInput creation itself is retried with backoff to ride out exactly that
kind of transient permission gap.
"""
import sys
import time

from evdev import UInput, ecodes as e

CAPABILITIES = {
    e.EV_REL: [e.REL_X, e.REL_Y],
    e.EV_KEY: [e.BTN_LEFT],
}

# Nudge periodically over a long window rather than a short fixed list —
# confirmed on a real cold boot that cage/Chromium can take 20+ seconds to
# actually report started, so a handful of early attempts isn't enough.
NUDGE_INTERVAL_SECONDS = 5
NUDGE_DURATION_SECONDS = 90

# Retries opening the virtual mouse device itself, separate from the
# nudge-timing retries above — this is for the case where /dev/uinput
# isn't fully ready yet (module just loaded, permissions not applied).
UINPUT_OPEN_RETRIES = 5
UINPUT_OPEN_RETRY_DELAY_SECONDS = 1


def nudge(ui):
    ui.write(e.EV_REL, e.REL_X, 1)
    ui.write(e.EV_REL, e.REL_Y, 1)
    ui.syn()
    time.sleep(0.1)
    ui.write(e.EV_REL, e.REL_X, -1)
    ui.write(e.EV_REL, e.REL_Y, -1)
    ui.syn()


def open_uinput():
    last_error = None

    for attempt in range(UINPUT_OPEN_RETRIES):
        try:
            return UInput(CAPABILITIES, name="atlas-virtual-mouse")
        except Exception as error:
            last_error = error
            time.sleep(UINPUT_OPEN_RETRY_DELAY_SECONDS)

    print("hud_cursor_fix: could not open /dev/uinput:", last_error, flush=True)
    return None


def main():
    ui = open_uinput()

    if ui is None:
        return

    try:
        with ui:
            elapsed = 0.0

            while elapsed < NUDGE_DURATION_SECONDS:
                time.sleep(NUDGE_INTERVAL_SECONDS)
                elapsed += NUDGE_INTERVAL_SECONDS
                nudge(ui)
    except Exception as error:
        print("hud_cursor_fix: nudge failed:", error, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("hud_cursor_fix: unexpected failure:", error, flush=True)

    # Never fail the service over a cosmetic fix — a stuck cursor is
    # vastly preferable to Restart=on-failure yanking down an otherwise
    # working HUD because of it.
    sys.exit(0)
