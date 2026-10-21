# The activity log (Phase 1)

Pikina is learning your routines so it can later suggest your next step. Phase 1 only
**collects** data. Nothing is predicted or suggested yet, and nothing leaves your computer.

## What is recorded

**Commands you run through Pikina.** These are on by default. Each one becomes a row in
`activity.db` in your Pikina data folder (`~/.local/share/pikina/` on Linux).

| Field | Example | Notes |
|:---|:---|:---|
| time | 2026-10-21 09:04 (stored in UTC; hour and weekday in local time) | |
| action | `app.open:code`, `fs.find_file:*.pdf`, `todo.add` | A normalised key, never what you typed |
| source | `typed`, `quick_panel`, `cli`, `alias`, `ui`, `backfill` | Where the command came from |
| outcome | `ok`, `error`, `denied` | Failed or declined actions are kept but won't be learned as habits |
| active app | `code` | The app you were in when you ran the command. Only filled in when app tracking is on |
| session | `s-3fa2c1b04e` | A new session starts after 30 minutes with no activity |

**The app in focus.** This is **off until you turn it on**, in Settings or with
`POST /api/activity/settings`. It records the app's name each time the focused app changes.

- It records **app names only, never window titles.**
- Focus that lasts under a second is ignored.
- Pikina's own windows are ignored.

## What is never recorded

- **What you typed.** Logged instead: the action key only.
- **Content.** To-do text, event titles, search words, file names, paths, calculator input,
  memory queries, clipboard text and your name are all dropped. A file search keeps only the
  file type, so `find file tax_return.pdf` becomes `fs.find_file:*.pdf`.
- **Unknown apps.** An app you open is logged by name only if it's a known app or an
  installed program. Otherwise it's `app.open:?`.
- **Alias names.** An alias is logged by a keyed hash (`alias.run:3f9c0a1b77de`). The key is
  random on each install and is never exported, so the hash can't be reversed with a word
  list. The `activity` command shows the name again for the aliases you have now.
- **Plugin parameters.** Plugin commands are logged by their command name only.
- **Undos.** The steps Pikina undoes after an alias fails are not logged.
- **Excluded apps.** Password managers, unlock prompts, authenticator apps and Tor Browser
  are written as `excluded`. This applies both when they're in focus and when you open one
  (`app.open:excluded`). Their name is not kept, and that time isn't credited to another app.
  Add your own, such as a banking app, to `"activity_exclude_apps"` in `settings.json`, for
  example `["MyBank", "Vivaldi"]`. Channel variants such as `vivaldi-stable` are covered too.
- **Sites opened as apps.** A Chrome, Brave or Edge "app window" for a website is recorded as
  the browser (`chrome`), never as the site. Excluding the browser covers its app windows.

### What this can't detect

- **Private windows.** Firefox private windows count as excluded, but only in an English
  Firefox: the window title is checked in memory and never stored. **Chrome, Brave and Edge
  incognito windows look exactly like normal windows** from outside the browser. If that
  matters, add the browser to your exclusion list.
- **Websites.** Pikina sees app names, not pages. A banking site in Firefox shows up as
  `firefox`.

## Where tracking works

| Session | App tracking |
|:---|:---|
| Hyprland | Yes. Pikina listens to Hyprland's event socket, so there is no polling |
| Sway / i3 | Yes, through the i3 IPC socket |
| X11 (any window manager) | Yes, needs `xprop` (Arch package: `xorg-xprop`) |
| Windows | Yes. Checks the foreground app once a second |
| GNOME or KDE on Wayland | Not yet. These desktops don't expose the active window to other programs; the HUD says so |

## If settings.json is broken

If you hand-edit `settings.json` and leave a typo, Pikina **pauses all recording** until you
fix or delete the file. The title-bar pill shows `! PAUSED` and explains why. A typo never
lifts a pause or drops your exclusions. Only a real `true` or `false` counts:
`"activity_logging": "false"` (a string) counts as off.

## Controls

| Command | What it does |
|:---|:---|
| `activity` / `activity this week` | Today's or this week's actions, sessions, top actions and apps |
| `pause activity` / `resume activity` | Stops recording everything (commands and app switches) until you resume, even across restarts. A `paused` marker is written so that time isn't credited to the last app |
| `forget my activity` | Deletes every recorded action and app switch. Asks first |

- **In the dashboard,** the ● REC / ❚❚ PAUSED pill in the title bar pauses or resumes with
  one click. It shows ○ OFF when something is switched on but nothing can be recorded, such as
  after the kill switch or when app tracking can't run in your session. Settings has the two
  on/off switches, and they are independent: you can track apps without logging commands.
- **In the tray,** there is a *Pause activity log* item.
- **In the terminal,** `pikina` then `activity export ~/activity.json` saves everything,
  and `activity backfill` re-imports history from the audit log.

## Retention and history

- **Raw rows are deleted after 90 days.** Later phases will keep only counts.
- **Backfill.** On first start, Pikina rebuilds up to 90 days of past commands from its
  audit log (the replay log), so learning doesn't start from zero.
  - It imports only what happened **before live recording began**, so anything done while
    paused or with logging off is never imported later.
  - It extracts the same normalised keys and copies nothing else.
  - Backfilled rows have the source `backfill`, because the old log can't tell the dashboard
    from the quick panel.
- **Forget is final.** After `forget my activity`, nothing from before that moment comes
  back, not even with `activity backfill`. Deleted rows are overwritten on disk.
- **The `activity` command's own reply** (your top apps and actions) is kept out of the
  audit log.

## The Phase 1 gate

Phase 2 (pattern detection) starts only after **two weeks of clean data**. Check it with
`activity this week`:

- most actions should be `ok`;
- the top actions should be things you really do;
- nothing personal should show up.

If something looks wrong, that's a bug. Report it rather than working around it.
