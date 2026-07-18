# A.T.L.A.S. — Command Reference

Every command below is triggered by saying **"Hey Atlas"** then the phrase.
Phrases are flexible — several wordings map to each action; a representative
one is shown. "Local" = zero API/token cost. Items needing the PC companion
or phone link are marked.

---

## PC power & Wake-on-LAN
| Say | Does | Cost |
|-----|------|------|
| "boot my PC" / "wake my PC" / "power on my PC" | Sends Wake-on-LAN magic packets, then tells you if it actually came up | Local |
| "why won't my PC wake?" | Diagnoses WoL (finds the USB-adapter root cause, WiFi egress, next steps) | Local |

## Timers, reminders & focus
| Say | Does | Cost |
|-----|------|------|
| "set a timer for five minutes" (words or numbers) | Countdown on the reactor; chime + "time's up" + amber flash at zero; survives a restart | Local |
| "cancel the timer" / "how long left on the timer?" | Cancel / check remaining | Local |
| "remind me in twenty minutes to check the oven" | Schedules a spoken reminder (fires even in quiet hours) | Local |
| "focus mode" / "focus mode for 45 minutes" | Dims HUD to the reactor, mutes proactive nudges, spoken wrap-up | Local |
| "end focus mode" | Ends it early | Local |

## Notes
| Say | Does | Cost |
|-----|------|------|
| "take a note buy more filament" | Saves a note | Local |
| "read my notes" / "clear my notes" | Read back / wipe | Local |

## Briefings & news
| Say | Does | Cost |
|-----|------|------|
| "morning briefing" / "brief me" | Weather, reminders, notes, PC, printer, Pi health, headlines (also auto once each morning) | Local (free news) |
| "what's in the news?" | Top 3 spoken headlines | Local (free news) |

## Network & devices
| Say | Does | Cost |
|-----|------|------|
| "what's on my network?" / "list the devices on my network" | Speaks the device roster (count + identified names) | Local |
| "secure my network" | Audit: unknown devices, exposed LAN services, repeated failed SSH — proposes isolation, never blocks | Local |

## Camera security gate
| Say | Does | Cost |
|-----|------|------|
| "learn my face" | Enrolls you (burst capture, ~15 quality crops) as the authorized user | Local |
| "were there any unauthorized users while I was gone?" | Yes/no + count; opens security HUD, shows each photo 10s with what they tried, then clears the alert | Local |
| "camera gate on" / "camera gate off" | Toggle face verification | Local |

> When active, a wake-up after 10+ idle minutes does a quick face check.
> Strangers get restricted mode (time/timers/reminders only) and a saved
> photo + a record of what they attempted.

## Diagnostics, logs & self-repair
| Say | Does | Cost |
|-----|------|------|
| "run diagnostics" / "system check" | Services, disk/mem/temp, internet, mic, PC, printer, budget — one spoken verdict | Local |
| "what went wrong?" / "any recent errors?" | Answers from the persistent interaction log (error rate, latency, wake confidence) | Local |
| "get the whole system healthy" | Full sweep: diagnose → safe-repair only what's broken → verify → backup snapshot → update check → report | Local |
| "how's the internet?" | Real ping + DNS timing, honest verdict | Local |

## Printer (3D)
| Say | Does | Cost |
|-----|------|------|
| "how long left on the print?" | Print ETA (extrapolated from progress) | Local |
| "emergency stop the printer" | Pauses the print immediately | Local |

*(Existing printer status/pause/resume/cancel commands still work.)*

## HUD & screen
| Say | Does | Cost |
|-----|------|------|
| "lights out" / "go dark" | Fades the HUD to near-black | Local |
| "lights up" | Restores it (any wake-up also restores) | Local |
| "stand down" / "all clear" | Acknowledges/clears a red alert | Local |

## Self-upgrade (gated)
| Say | Does | Cost |
|-----|------|------|
| "add calendar support to yourself" | Creates a rollback point and reports readiness — the actual build waits for your explicit approval (agent run costs tokens) | Local |

## Chief of staff
| Say | Does | Cost |
|-----|------|------|
| "what am I forgetting this week?" / "what's on my plate?" | Deadline-sorted rundown of reminders, task-notes, and a local calendar file | Local |

## Emergency
| Say | Does | Cost |
|-----|------|------|
| "initiate emergency shutdown" | Confirm-gated: preserve data → pause print → log → shutdown in 1 min | Local |
| "cancel shutdown" | Aborts the pending shutdown | Local |

## PC control — needs the Windows companion (Setup Guide Part 1)
| Say | Does | Cost |
|-----|------|------|
| "open Fusion" | Launches Fusion 360 | Local |
| "what's open on my PC?" | Lists open window titles | Local |
| "show me my PC screen" | Screenshots the PC onto the HUD | Local |
| "show me the newest screenshot" | Opens/returns your latest screenshot | Local |
| "volume up / down / mute" | PC volume (media keys) | Local |
| "play / pause / next track / previous track" | PC media control | Local |
| "find me videos showing how to replace an AD5X nozzle" | YouTube search on the PC, fullscreen, Shorts filtered | Local |
| "how's my PC?" | PC CPU/RAM/disk/uptime, flags issues | Local |
| "clean up my PC" | Runs the whitelisted temp-file cleanup | Local |

## Phone link — needs PHONE_TOKEN + Tailscale (Setup Guide Part 5)
Called from your phone over HTTP, not by voice:
| Endpoint | Does | Cost |
|----------|------|------|
| `POST /phone/ask` | Ask a question (continues the desk conversation) | Model (on-demand) |
| `GET /phone/status` | Weather, PC/printer, device count, intruder count | Local |
| `GET /phone/events` + `/phone/event_photo/<id>` | Review away-mode intruders | Local |
| `POST /phone/camera` | "What does the camera see?" (described at the desk) | Model (on-demand) |
| `POST /phone/pc/<action>` | open_fusion / screenshot / youtube / apps | Local |

---

**Token cost summary:** everything is local/zero-token except general
knowledge questions (the normal voice Q&A), `/phone/ask`, and
`/phone/camera` — and those only when you invoke them, never continuously.
