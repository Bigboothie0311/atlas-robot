# A.T.L.A.S. — Command Reference

Say **"Hey Atlas"** then the phrase. Wordings are flexible — several map to
each action. "Local" = zero API/token cost. Items needing the PC companion
or phone link are marked. Ask **"what can you control?"** anytime for a live
spoken version of this list.

---

## Everyday
| Say | Does | Cost |
|-----|------|------|
| "what time is it" / "what's the date" | Time, date, uptime | Local |
| "set a timer for five minutes" | Countdown + chime; survives restart | Local |
| "cancel the timer" / "how long left on the timer" | Manage the timer | Local |
| "remind me in twenty minutes to…" | Spoken reminder | Local |
| "focus mode" / "focus mode for 45 minutes" / "end focus mode" | Dim HUD, mute nudges | Local |
| "take a note …" / "read my notes" / "clear my notes" | Notes | Local |
| "I'm leaving" / "goodbye atlas" | Darkens HUD, shuts down PC, arms face gate | Local (+PC) |

## Memory & planning
| Say | Does | Cost |
|-----|------|------|
| "remember that …" | Saves a fact/preference (explicit only) | Local |
| "what do you remember about X" | Recalls facts on a topic | Local |
| "forget about X" / "forget everything" | Targeted or full forget | Local |
| "prioritize X" / "make X a priority" | Adds a priority | Local |
| "what are my priorities" | Lists priorities | Local |
| "what's my day" / "today" | Weather + reminders + priorities + notes | Local |
| "what am I forgetting this week" | Weekly rundown | Local |

## Information
| Say | Does | Cost |
|-----|------|------|
| "morning briefing" / "brief me" | Full rundown (auto once each morning) | Local |
| "what's in the news" | Top headlines | Local |
| "sky watch" / "what's up in the sky" | Meteor showers, launches, moon, stargazing | Local |
| "any meteor showers" · "next rocket launch" · "is it good for stargazing" · "what's the moon phase" | Individual sky reports | Local |
| "what's the weather" / "will it rain tomorrow" | Weather | Local |

## Network & security
| Say | Does | Cost |
|-----|------|------|
| "what's on my network" / "list the devices on my network" | Device roster | Local |
| "secure my network" | Unknown-device / exposed-service / SSH audit | Local |
| "learn my face" | Enroll as authorized user | Local |
| "do I have any intruder alerts" / "were there any unauthorized users" | Review captures + what they tried | Local |
| "camera gate on" / "camera gate off" | Toggle face verification | Local |

> **Face gate is now self-managing:** it trusts you for 1 hour and only
> re-verifies on "I'm leaving", when your phone leaves the LAN, or hourly.
> An unrecognized face re-triggers verification every wake until you clear
> it.

## Diagnostics & self
| Say | Does | Cost |
|-----|------|------|
| "status report" / "sitrep" | Cinematic full system + security readout | Local |
| "run diagnostics" / "system check" | Service/sensor/budget self-check | Local |
| "check connections" / "is everything connected" | Wi-Fi, PC link, companion, Tailscale | Local |
| "heal yourself" / "self heal" | Detect + safely repair failures, report | Local |
| "get the whole system healthy" | Full diagnose → repair → backup → report | Local |
| "what went wrong" / "any recent errors" | Answers from the persistent log | Local |
| "how's the internet" | Ping + DNS timing | Local |
| "what did you hear" | Last transcript | Local |
| "what can you control" | Lists real capabilities | Local |
| "what would happen if I said …" | Command simulator (no execution) | Local |
| "check your tools" / "upgrade whisper" | Tool versions / gated upgrade proposal | Local |
| "list my achievements" | Easter-egg progress | Local |

## Speech tuning
| Say | Does | Cost |
|-----|------|------|
| "when I say X I mean Y" | Teach a persistent alias (safe targets only) | Local |
| (automatic) "Did you mean X?" | One-shot clarify on a mis-hear, before the model | Local |

## HUD & screen
| Say | Does | Cost |
|-----|------|------|
| "go dark" / "lights out" / "lights up" | Dim/restore the HUD | Local |
| "stand down" / "all clear" | Clear a red alert | Local |

## PC (needs the Windows companion)
| Say | Does | Cost |
|-----|------|------|
| "boot my PC" / "wake my PC" | Wake over the direct Ethernet link | Local |
| "why won't my PC wake" | WoL diagnosis | Local |
| "shut down my PC" / "cancel PC shutdown" | Shutdown (confirm) | Local |
| "open Fusion" · "open Spotify" · "open Claude" | Launch approved apps | Local |
| "work mode" / "design mode" / "game mode" | App profile: apps + volume + focus | Local |
| "what's open on my PC" | Window list | Local |
| "show me my PC screen" / "show me the newest screenshot" | To the HUD | Local |
| "volume up / down / mute" · "play / pause / next track" | Media control | Local |
| "find me videos showing how to …" | YouTube search, fullscreen, no Shorts | Local |
| "how's my PC" / "clean up my PC" | Health report / temp cleanup | Local |

## Emergency
| Say | Does | Cost |
|-----|------|------|
| "initiate emergency shutdown" | Confirm-gated safe shutdown sequence | Local |
| "emergency stop the printer" | Pauses the print | Local |

## Printer (status only)
| Say | Does | Cost |
|-----|------|------|
| "how long left on the print" | Print ETA | Local |

## Phone link (HTTP over Tailscale, not voice)
`POST /phone/ask` · `GET /phone/status` · `GET /phone/events` · `POST /phone/camera` · `POST /phone/pc/<action>` — see PHONE_LINK.md.

---

**Token cost:** everything is local except general knowledge questions,
`/phone/ask`, and `/phone/camera` — and only when you invoke them.

There are also hidden easter-egg phrases to discover. 🙂
