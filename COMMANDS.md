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
| "add milk to my shopping list" / "what's on my shopping list" / "remove milk from my shopping list" / "clear my shopping list" | Shopping list | Local |
| "flip a coin" / "roll a die" / "roll two dice" / "roll a d20" | Coin flip & dice roll | Local |
| "convert 10 miles to kilometers" / "what's 100 fahrenheit in celsius" | Unit conversion (temperature, distance, weight) | Local |
| "how many days until christmas" / "how many days until march 5th" | Countdown to a date or holiday | Local |
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

> **Same capabilities everywhere:** diagnostics, self-heal, system health,
> connection checks, storage, recent errors, tool status, and "what can you
> control" are also available as a direct model tool call
> (`run_atlas_diagnostic_or_repair`), so they work from any surface — voice
> fallback or the phone link — even when your exact phrasing doesn't match
> one of the trigger phrases above. You should no longer hear "I don't have
> access to that" for anything on this list.

## Diagnostics & self
| Say | Does | Cost |
|-----|------|------|
| "status report" / "sitrep" | Cinematic full system + security readout | Local |
| "run diagnostics" / "system check" | Service/sensor/budget self-check | Local |
| "check connections" / "is everything connected" | Wi-Fi, PC link, companion, Tailscale | Local |
| "heal yourself" / "self heal" | Detect + safely repair failures, report; also rebuilds/redownloads the Whisper binary or model from their own vendored build/download scripts if missing, and flags recent phrases it misrouted to the model | Local |
| "get the whole system healthy" | Full diagnose → repair → backup → report | Local |
| "what went wrong" / "any recent errors" | Answers from the persistent log | Local |
| "how's the internet" | Ping + DNS timing | Local |
| "what did you hear" | Last transcript | Local |
| "what can you control" | Lists real capabilities | Local |
| "what would happen if I said …" | Command simulator (no execution) | Local |
| "check your tools" / "upgrade whisper" | Tool versions / gated upgrade proposal | Local |
| _asking for something that needs a missing tool_ (e.g. "make a QR code", "translate this") | Offers to acquire the tool: spoken yes/no → PyPI due-diligence → install → reports location → refreshes graph | Local + PyPI |
| "list my achievements" | Easter-egg progress | Local |

## Speech tuning
| Say | Does | Cost |
|-----|------|------|
| "when I say X I mean Y" | Teach a persistent alias (safe targets only) | Local |
| (automatic) "Did you mean X?" | One-shot clarify on a mis-hear, before the model | Local |

## Macros — teach your own commands
| Say | Does | Cost |
|-----|------|------|
| "when I say X do Y" · "when I say X you should Y" · "if I say X then Y" · "teach you that X means Y" | Teaches a phrase that replays one or more existing commands | Local |
| "…do Y and then Z" | Chains up to 5 actions per macro | Local |
| "list my macros" / "what commands have I taught you" | Lists everything you've taught | Local |
| "forget the macro X" / "unlearn X" | Removes a taught macro | Local |

> Macros replay real commands — they can't teach brand-new behavior, and
> teaching over an existing built-in phrase (like "flip a coin") is refused
> so you can't accidentally shadow it. A macro that loops back on itself
> (directly or through another macro) is stopped after one hop.

## HUD & screen
| Say | Does | Cost |
|-----|------|------|
| "go dark" / "lights out" / "lights up" | Dim/restore the HUD | Local |
| "weather radar" / "pull up the weather" / "show me the weather radar" / "weather hud" | Opens the full-screen weather + live NWS radar loop overlay | Local |
| "close the weather" / "close the radar" / "hide the weather" | Closes the weather/radar overlay | Local |
| "brighten the screen" / "raise the brightness" | Overrides quiet-hours dimming back to full brightness | Local |
| "normal brightness" / "lower the brightness" | Restores normal quiet-hours dimming | Local |
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

## Self-showcase content
| Say | Does | Cost |
|-----|------|------|
| "make a promo video of yourself" / "record yourself a reel" / "make an Instagram reel" | Records a narrated, varied tour of **Atlas's own HUD screen** — weather radar and self-diagnostics always show up, phrasing and a few extra feature beats are randomized each time so it's not the same script and clip twice in a row — narrated (rendered silently, never spoken aloud — normal speech resumes right after) — and edits it into a 9:16 Reel with a draft caption. Doesn't publish anything. | Local |
| "make a video where you say ..." / "record a reel about ..." (custom script) | Not stuck to the default tour — describe any narration lines, in any order, and Atlas records and edits exactly that script instead. | Local |
| (sometimes, randomly, part of the default tour; or ask directly) show what he can do on the PC | Can hop over to a real clip of the **Windows PC's own screen** for a beat or two — opening a YouTube video, opening an app — then hop back to his own HUD, and stitch both sources into one final Reel. Requires the PC connection to be configured. | Local |
| "post that to Instagram" / "publish the reel" | Publishes an exact finished Reel + caption to the account. Public and irreversible — always confirms the exact media and caption first. | Local |

> The physical camera faces the room, not Atlas, so "record yourself" means
> a narrated recording of his own HUD kiosk display by default, not literal
> self-video — see the `self_record_clip` note in `capabilities.py`. Using
> a PC screen recording as a *stand-in* for "record yourself" was tried
> first and rejected: it showed the owner's Windows desktop, not Atlas.
> Separately, individual beats can still deliberately splice in a real PC
> screen clip alongside the HUD ones — that's an intentional additional
> source, not the same mistake. Publishing briefly exposes just the one
> finished video file over Tailscale Funnel (not a port-forward) so
> Instagram's servers can fetch it, and tears that exposure back down
> immediately after — nothing stays publicly reachable outside that one
> request.

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
