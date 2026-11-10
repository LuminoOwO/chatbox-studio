# Changelog

## 1.30
- **AMD and integrated graphics, properly:** a second speech engine, **Vulkan**
  (whisper.cpp), runs speech recognition on any graphics chip with a Vulkan driver - every
  AMD Radeon (also the ones ROCm doesn't support), **AMD APUs** (Radeon 680M / 780M / 890M),
  **Intel Arc** and **Intel Iris Xe / UHD** graphics. Nothing to install besides your normal
  graphics driver; on Linux it's the default for AMD because it works on every distribution.
  - The setup lists every chip with its engine ("NVIDIA CUDA", "AMD ROCm", "Vulkan") and
    shared memory for integrated graphics, suggests lighter models there, and downloads the
    smaller whisper.cpp models (e.g. large-v3 1.1 GB instead of 3.1 GB).
  - Mix it with everything else: e.g. speech on an NVIDIA card and Earshot's listening model
    on the iGPU. The translator and smart AI don't use Vulkan and run on the CPU then.
  - Live memory and load in *Graphics cards* for Vulkan chips too (AMD on Linux: amdgpu).
  - The engine is built from whisper.cpp 1.9.5 by the repository's GitHub workflow.
- **ROCm on more AMD chips (Linux):** APUs and smaller RDNA2/RDNA3 cards ROCm only runs as a
  close relative (Radeon 780M, 680M, RX 6600/6700, ...) get `HSA_OVERRIDE_GFX_VERSION`
  automatically; the chip type is read from the driver. APUs count their shared memory.
- **Linux packages for every major distribution:** `.deb` (Debian, Ubuntu, Mint, Pop!_OS),
  `.rpm` (Fedora, openSUSE, RHEL-likes) and an Arch package (+ `PKGBUILD`), next to the ZIP.
  They install to `/opt/chatbox-studio`; the first start prepares the per-user environment
  with a progress window - with a standalone Python 3.12 when the system's doesn't fit.
- **No preferred language:** the default route only writes down English as you say it; the
  Quick start asks which language you speak and which one the chatbox should show. Language
  lists are alphabetical after English, presets no longer suggest language pairs.
- **Windows:** the Linux-only value categories (WiVRn, wireless device batteries) aren't
  offered anymore. On Linux the category is now just called *Devices*.
- First start with several graphics cards picks the smallest one that fits a speech model.
- Packages and releases are built by GitHub Actions on every version tag.

## 1.29
- **Start log** (`starts.log` next to `crash.log`, in `~/.local/share/chatbox-studio` /
  `%LOCALAPPDATA%\Chatbox Studio`): every start - and every time a second start brings the
  open window to the front - is written down with the time and, on Linux, which program
  started it (the chain of parent processes) and session hints. For finding out what starts
  the app when you didn't.
- A second start waits longer for a busy open window (e.g. while a model loads) before it
  decides none is open - before, it could open a second copy.

## 1.28
- **Graphics cards panel shows every card Chatbox Studio uses** - also the ones whose model
  is still loading, not installed yet or failed (e.g. the translator set to your second
  card). Each card lists what runs on it ("not loaded yet" when it isn't there yet).
  New option in *⚙ Settings › General*: *…also cards Chatbox Studio doesn't use*.
- **A graph per card** instead of the load bar: load (green area, on Windows with Chatbox
  Studio's share in colour) and memory use (line) over the last 3 minutes.
- **Fixed: the smart translation download failed** ("401 … Repository Not Found"): the
  Qwen2.5 models were removed from huggingface.co/ctranslate2-4you. They now come from
  huggingface.co/jncraton (same models, CTranslate2 int8). Download errors are short now.

## 1.27
- **Windows: a real setup program** - `Chatbox-Studio-1.27-Windows-Setup.exe`. Double-click,
  Install, and the Chatbox Studio Setup window opens. Per user, no admin rights; updates an
  existing install. The ZIP with Install.bat is still there (for PCs where Smart App
  Control blocks unsigned programs).
- **Simpler setup window:** first a choice of *Standard* (speech recognition + translator,
  the default), *Speech only*, *Keep what I have* (updates) or *Custom…*. Custom has
  everything, sorted into speech, translator, graphics card support, extras and Earshot.
  One line with the download size and the memory meters at the bottom; the detailed disk
  meter is in Custom.
- **Lighter default:** no Earshot and no AI model by default; the Standard translator is
  NLLB 600M (1.3B is in Custom).
- **Earshot's conversation features are off and marked experimental** - they made
  recognition and translation worse rather than better:
  - *Hint Whisper with the conversation* is off (also for existing settings, once). When you
    switch it on, it now only uses your own sentences from the last minute - other people's
    lines (often misheard game audio in another language) pulled Whisper towards wrong
    words and the wrong language.
  - *Smart translation* (local AI) is off (also for existing settings, once).
  - Earshot's listening, room language, echo guard and showing what others say stay as
    they were.
- Fixed: the setup's Install button could stay enabled for a choice that doesn't fit.

## 1.26
- **Live graphics card usage in the main window** (*Graphics cards*, under VRChat): for every
  card your models run on - also both an NVIDIA and an AMD card at once:
  - memory: Chatbox Studio (colour), other programs like VRChat (grey) and what's free,
  - load in % (on Windows also Chatbox Studio's share), and the temperature,
  - hover a card for the models on it and the programs using its memory.
  Updates every 2 seconds, pauses while the window is minimised. Switch it off in
  *⚙ Settings › General*. Runs on the CPU only: says so instead.

## 1.25
- **Live graphics memory in the setup:** each card's meter now shows what other programs use
  right now (grey) next to what Chatbox Studio will use, and how much is left. Open the
  setup while VRChat runs to see what's realistically free while you play.
  - Hover a meter for the list of programs (VRChat, browser, desktop…) and their memory.
  - Updates every 3 seconds; the fit check uses the live numbers and warns before installing
    models that won't fit next to VRChat.
  - A Chatbox Studio that's already running counts as free (its models are being replaced).
  - NVIDIA: nvidia-smi. AMD on Linux: the amdgpu driver (sysfs, per program from fdinfo).
    Windows (all cards): the same counters Task Manager uses.

## 1.24
- **Multi-GPU, any vendor mix:** choose per AI model which card it runs on - speech
  recognition, translator, Earshot's smart AI and (new) Earshot's own listening model.
  NVIDIA and AMD can be mixed freely, e.g. speech on an RTX card and the Qwen smart AI on a
  Radeon. Models on AMD cards run in a helper process with the ROCm build of CTranslate2;
  NVIDIA and CPU models stay in the app. The helper stops when the app closes.
- **Setup:** a *… runs on* choice for each model, one graphics-memory meter per card (with
  what's on it), the fit check per card, and the NVIDIA / AMD support parts ticked
  automatically for the cards you use.
- **Earshot listening model:** with its own Whisper model (small / medium / large) on
  another device, Earshot listens to the game while you talk instead of waiting.
- The AMD engine now lives in its own folder (`engines/rocm` in the app data) instead of
  replacing the app's engine. Installs from 1.22/1.23 are moved over by the setup
  automatically (update or *Install*).
- Settings from before 1.24 (one GPU index, *Same GPU as Whisper*) keep working.

## 1.23
- **New ⚙ Settings window** (top right, Ctrl+,): theme and text size, keyboard shortcuts,
  speech model and device, translator, VRChat ports, filters, parts and about - one page per
  topic, with a search box. The main window keeps only what you change while playing:
  microphone, voice threshold, pause length, *Send to chatbox*, *Pause when muted*.
- **Getting started checklist** on the main window: speech model loaded, microphone hears
  you, VRChat connected, translator installed (when your routes need it) - each with a button
  that fixes it. It hides itself when everything is done (and can be turned off).
- **Quick start** on the first start: pick your microphone (with a live level), your
  languages (one click), check VRChat with a test message.
- **Guided tour** (❔ Help › Show me around): dims the window and explains each part, one
  after the other.
- **❔ Help menu** (F1): quick start, tour, shortcuts, the manual, and *Copy the log* for bug
  reports.
- The log is folded away under the tabs; its latest line is always shown.

## 1.22
- **AMD graphics cards (Windows and Linux):** Radeon RX 6800 and newer, RX 7000 and RX 9000
  (RX 6800 and newer, RX 7000, RX 9000) now run the AI on the card via ROCm, using the official ROCm build of
  CTranslate2 (4.8.4, from its GitHub release - only the one file needed is downloaded).
  - Windows: AMD's ROCm 7.2.1 runtime packages are installed with it (needs the Adrenalin
    driver 26.2.2 or newer).
  - Linux: uses AMD's ROCm 7.2 libraries. If they're missing, the setup offers to install
    them from AMD's apt repository in a terminal (Ubuntu 22.04/24.04, Mint 21/22).
  - If the AMD engine doesn't load, the standard engine is put back and the app runs on the
    CPU, so it never breaks.
  - AMD cards show up in the app's Device list; GPU usage, VRAM, temperature, power, fan and
    clock work as chatbox values on Linux.
- **Setup window:** an always visible footer with the **graphics memory** your choice needs
  on the chosen card (speech + translator + smart AI), the **disk space used now** and
  **after the install** (plus download size and free space).
- **Fresh install…**: reinstalls every ticked part and model from scratch, optionally with
  fresh settings.
- **Uninstall completely…**: removes the app, its Python, shortcuts and menu entries, and if
  you like your settings and downloaded models.

## 1.21
- **New setup window (Windows and Linux): pick what you install.** After Python is set up,
  *Chatbox Studio Setup* opens. It shows your graphics cards, RAM and free space, lets you
  choose where the AI runs, and has a tick box for every part: NVIDIA support (CUDA), now
  playing and program audio, VR headset values, Earshot, plus the speech model, the
  translator and the smart translation AI. It checks whether your choice fits into your
  card's memory, shows the download size, and installs with a progress bar.
- Open it again any time to add or **remove** parts (frees the space): *Chatbox Studio
  Setup* in the menu / Start menu, *Modify* in Windows' Apps list, or *Appearance › Add or
  remove parts…* in the app. Updates open it with your current parts ticked.
- Without a desktop, or with `CBS_DEFAULTS=1` (Linux) / `Install.bat -Defaults` (Windows), the
  recommended parts are installed without asking.
- The Python packages are split into parts (`src/req/`); `requirements.txt` still installs
  everything for manual setups.
- Fix: an empty `XDG_CONFIG_HOME` / `XDG_DATA_HOME` no longer puts settings in the current
  folder.

## 1.20
- **New: 🎧 Earshot, a conversation-aware mode.** Earshot also listens to the game (what
  the people around you say), transcribes it on the same GPU while you're quiet, and keeps a
  short memory of the conversation, to translate you better:
  - Whisper gets the recent conversation as a hint: names and words people just used are
    recognised more often.
  - **🎧 Room language** as a route target: you're translated into the language the people
    around you speak, switched automatically. *Add room-language routes* sets it up.
  - Speaker echo guard: sentences your mic only picked up from the game aren't sent.
  - What others say is shown in the Earshot tab, translated into your language (never in
    the chatbox). Save the conversation if you like; nothing is stored otherwise.
  - **Smart translation (optional):** a local AI model (Qwen2.5 3B/7B on CTranslate2, or any
    CTranslate2 chat model folder) translates with the conversation in mind. Falls back to
    NLLB/Whisper. Marked ✨ in the transcript.
  - Hears VRChat only (Linux, PipeWire), everything you hear (Linux and Windows loopback), or
    any input device (e.g. a virtual cable).
- New chatbox block: *Language people around you speak*.
- Ctrl+3 opens the Earshot tab.

## 1.19
- **The chatbox is now built from blocks.** Every value is its own block - {gpu_name},
  {time}, {title}, {hmd_battery}, ... - next to speech, text blocks, line breaks and rotating
  messages. The block list (left) has everything that can go into the chatbox, by category,
  with a search box and what each value reads right now. Drag blocks into the chatbox where
  you want them, click to change, drag to move, drag back to the list (or Delete) to remove.
  Undo with Ctrl+Z.
- **Text blocks with values inside:** drop a value onto a text block, e.g. "CPU: {cpu}%".
  The text then only shows while all its values have something. [ ] parts still work.
- **Priority is a number per block:** lower = more important. Speech is 1, everything else 2.
  When room runs out, the highest number goes first, and all blocks with the same number go
  together. 0 = never removed.
- Scrolling (ticker, marquee, bounce, lines, pages, typewriter) is set per block.
- Block options where they matter: time zone and style for clocks, date and name for a
  countdown, title length and filters for music, lyrics source, GPU number.
- Privacy buttons are per kind (Speech, Text, Time, Music, Status, PC, VR, VRChat).
- *Edit view* switch: show empty blocks and line breaks to grab them, or exactly what
  VRChat shows.
- Setups from 1.18 and older are converted automatically (text, icons, priorities,
  scrolling, time zones, countdowns, music and lyrics settings).

## 1.18
- **New Chatbox tab: build your chatbox by drag and drop.** The preview is the editor:
  drag elements from the list into the chatbox where you want them, click one to change it,
  drag it to move it, drag it out (or press Delete) to remove it. Keyboard: arrow keys pick,
  Alt+arrows move. Removed elements keep their settings in the list until you delete them.
- **The preview looks like VRChat's chatbox:** Noto Sans (bundled), black-and-white emoji
  like in game (Noto Emoji, bundled), the same text width and margins, centered, and a
  warning when more than 9 lines would show (VRChat cuts the rest). Measurements from the
  vrc-live-caption project's VRChat chatbox reference.
- **More scrolling:** Marquee (letters), Bounce (back and forth), Lines (move up line by
  line) and Typewriter, next to Ticker and Pages. Speech also gets Marquee and Typewriter.
- **Insert value** menu next to every format field, so no {names} need typing.
- **Emoji count as two** towards the 144 characters, like VRChat counts them, so messages
  with emoji are no longer cut off at the end in game.
- Faster updates are allowed: VRChat now takes about one update per second. *Update at most
  every* goes down to 1.0 s (new default 1.2 s).

## 1.17
- **Chatbox with VOR and other OSC routers:** the app now finds the port VRChat really
  listens on with OSCQuery and sends the chatbox there. With a router, VRChat is often
  started with `--osc=...` or moved off 9000, so messages sent to 9000 never arrived.
  Only VRChat on your own PC counts, not other people's on the same network.
- The log says where chatbox messages go ("Chatbox messages go to 127.0.0.1:9000 ...").
- The option is now called **Find VRChat by itself (OSCQuery)** and covers sending and
  listening. Clearer VOR hint: keep "Send to port" at 9000.

## 1.16
- **One element for text and values:** PC specs, PC stats and VR headset are merged into
  **Custom text & info**. Type anything, one line per chatbox line, and mix in any PC, VR or
  VRChat value from the categorised list below the text box (double-click adds it at the
  cursor). *Ready-made* adds finished lines; *+ Add* has shortcuts (PC specs, PC stats, VR
  headset, specs ticker). Existing elements are converted automatically, with their icon,
  priority and format.
- **Window: scroll or pages** for every element except speech: show the text in a fixed
  number of characters, as a ticker that moves along a few words at a time and starts over,
  or page by page. Window size, words per step and time per step are adjustable.
- Rotating messages can use the same values.
- A line whose values are all empty disappears; text in [ ] without a value (e.g. "[EN]")
  now stays as typed.
- Privacy buttons show the start of each custom text, so several are easy to tell apart.

## 1.15
- **WiVRn support** (Linux, no SteamVR needed): headset name, maker and model, refresh rate
  and the rates it offers, stream bitrate, video codecs, hand / eye / face tracking, headset
  mic and speaker format, "connected" and "in VR" (from WiVRn's D-Bus service).
- **Monado / WiVRn devices** through libmonado: headset battery and charging, left/right
  controller batteries, controller model (named after the headset, e.g. "Quest 3 Touch"),
  trackers, headset device name and serial. Found through the active OpenXR runtime.
- New **WiVRn** category in the value list, and new VR presets.
- **More PC specs**: BIOS version, architecture, running programs, CPU maker and L3 cache,
  CPU fan, GPU memory clock, power limit, PCIe link, VRAM usage %, system drive model and file
  system, all drives, drive read/write speed, SSD temperature, connection type (Wi-Fi /
  Ethernet), link speed, Wi-Fi signal, all screens and screen count; on Windows also RAM type
  (DDR4/DDR5), sticks (e.g. 4x8 GB) and maker.
- **Wireless device batteries** on Linux now also come from BlueZ, the kernel drivers
  (Logitech, DualSense/DualShock, Xbox via xpadneo, ...) and `headsetcontrol`, not only
  UPower. New "number of devices" value.

## 1.14
- Works next to **OSC routers like VOR** and other apps that use port 9001:
  - When port 9001 is taken, the app listens on a free port and announces it with
    **OSCQuery**, so VRChat sends the mute and AFK state to it as well. No setup needed.
  - New **Listen on port** setting (left panel, VRChat): add Chatbox Studio to your OSC router
    (host 127.0.0.1, e.g. port 9111) and enter the same port here.
  - Clearer status and log messages when the port is taken.

## 1.13
- New **VR headset** element. While SteamVR is running (it's never started by the app):
  headset maker and model, refresh rate, IPD, headset battery (wireless), tracking system,
  controller model and left/right battery, number of trackers, lowest tracker battery,
  tracker model, base stations, and "Full body" with 3+ trackers.
- Detects the VR software in use: SteamVR, Meta Quest Link, Virtual Desktop, ALVR, WiVRn,
  Monado, PICO Connect, Steam Link.
- **From VRChat** (over OSC): VR or desktop, tracking type (3-point, 4-point, full body),
  avatar eye height and scale, seated, earmuffs.
- **Audio**: current output device (headphones/speakers), microphone, volume, muted.
- **Wireless device batteries** on Linux: Bluetooth headphones, mouse, keyboard, gamepad,
  phone (via UPower).
- The value list in PC specs / PC stats / VR headset is now sorted into **categories**
  (System, CPU, Graphics, Memory, Storage, Network, Display, Power, VR headset, Controllers &
  trackers, Audio, Devices, VRChat), with a search box, "only available here", and a count
  of how many values each category has on this PC.
- VR, audio and device values are only read while a format uses them or the list is open.

## 1.12
- New **PC specs** element: operating system, version, kernel/build, desktop and X11/Wayland,
  CPU model (also short, e.g. "i5-12400"), cores and threads, max clock, graphics card(s),
  GPU driver, VRAM, RAM size (and speed on Windows), motherboard, PC/laptop model, screen
  resolution and refresh rate, system drive size. Works on Linux and Windows.
- **PC stats** got many more live values: CPU clock and temperature (Linux), GPU power, fan
  and clock, swap, disk usage and free space, download/upload speed, battery and charging
  state, uptime.
- Both settings pages show a table of every value with what it reads on your PC right now;
  double-click one to add it to the format. Many new format presets.

## 1.11
- Fixed a crash ("Internal C++ object (QPushButton) already deleted") when opening another
  element's settings, e.g. Play time, after having been on the Speech settings. The preview
  still referred to Speech's "Show sample" button, which no longer existed.
- Fixed rare random crashes ("Segmentation fault") while clicking around: Python's memory
  cleanup could start in a background thread and free parts of the window there. It now
  only runs on the window's own thread.

## 1.10
- **Live speech** (new default, replaces the queue): when you say something new, the chatbox
  jumps to it as soon as the line on screen has been shown once, instead of finishing all
  older pages first. The chatbox now stays in step with your voice. "Queue" is still there
  for anyone who wants every sentence shown in full.
- New "Send long talk every" slider (Audio): when you talk without a pause, a piece is sent
  at the latest after this time (default 10 s, was 20 s), so long monologues show up sooner.
- **Colour schemes**: Catppuccin Mocha, Macchiato, Frappé and Latte, Nord, Dracula, Gruvbox
  Dark, Tokyo Night and Rosé Pine, next to Dark, Light and High contrast. All meet the
  4.5:1 contrast guideline for small text.

## 1.9
- **New name: Chatbox Studio** (was VRC Voice Translator). Installing 1.9 over any older
  version moves settings, routes, filters, models and the Python setup over automatically,
  replaces the old shortcuts, menu entry and Windows Apps entry. Starting the new app
  directly also takes over old settings and models.
- **Icons per element**: choose any emoji or none for the clock, now playing, the "playing
  audio" fallback, stats and everything else. Older formats like "🕒 {time}" are converted.
- **Size sample**: changing "Characters shown at once" fills the preview with sample text of
  exactly that length; "Send sample to VRChat" shows it in game.
- **Lyrics (experimental)**: current line of the playing song, from local .lrc files or,
  if switched on, from the free LRCLIB database. Follows the now playing filters.
- **Accessibility**: Dark, Light and High contrast themes, text size from Small to Largest,
  clear keyboard focus outlines, higher contrast for small text, screen-reader names for
  icon buttons, keyboard shortcuts (Ctrl+P, Ctrl+H, Ctrl+L, Ctrl+1/2).

## 1.8
- **Priorities** for chatbox elements: Always keep, High, Normal, Low. When the chatbox runs
  out of space, the lowest priority makes room first (on a tie, the one lower in the list).
- While you talk, every element with a lower priority than speech is pushed away if the
  sentence needs the room. Elements with the same or higher priority stay, and the sentence
  pages through the space that's left. "Always keep" is never pushed away.
- Defaults: speech and AFK High; clock, countdown and custom text Normal; now playing,
  rotating messages, PC stats, play time and active app Low.
- The preview shows which elements are making room right now.

## 1.7
- New **Chatbox** tab: build your chatbox from elements and see a live preview with
  character count.
  - Speech / translation: characters shown at once, pages or scrolling for long text,
    time per page or scroll step, how long it stays, queue or jump to the newest.
  - Custom text, rotating messages, clock (local or any time zone, several possible),
    countdown, now playing, PC stats (CPU/RAM/GPU), AFK (from VRChat), play time,
    active app.
  - Formats with placeholders and optional parts, separators between elements, option to
    let speech replace everything else while it's shown.
- **Now playing** on Linux (MPRIS) and Windows (media controls), with the website detected
  from the browser window (YouTube, Twitch, SoundCloud, ...).
- **Filters** for now playing: hide or only allow programs, websites or titles, with a live
  list of what's detected right now and one-click buttons.
- **Fallback**: if audio plays without song info (e.g. a soundboard), the program is shown.
  Games and voice chat are ignored by default, and hidden websites never leak through.
- **Privacy bar**: hide any element instantly, or hide all extras at once; "Clear chatbox".
- Chatbox updates are rate-limited for VRChat and the message is cleared after speech ends.

## 1.6
- Fixed: the app sometimes ignored muting in VRChat. Since 1.4 the installer starts the app
  by itself, so a second copy could run and take VRChat's mute signal (port 9001) away from
  the window you were using. Now only one copy can run; starting it again brings the open
  window to the front.
- New status in the top bar shows the mute sync: "VRChat mic on", "Muted in VRChat",
  "waiting" (until you mute once) or "port 9001 busy" when another program holds the port.
- The app retries the port every few seconds, so mute sync starts working by itself once
  the other program is closed.
- "Pause when muted in VRChat" now switches on and off immediately (before, it only took
  effect after a restart).
- A sentence that's still being processed when you mute is no longer sent.

## 1.5
- Windows: fixed "An Application Control policy has blocked this file" during install.
  The installer now uses the official, signed Python from python.org instead of an unsigned
  copy, which Windows 11 "Smart App Control" refused to run.
- Windows: the installer checks that all libraries load and explains clearly what to do
  if Smart App Control still blocks one of them.
- Linux: no changes besides the version number.

## 1.4
- **Windows version** with a one-click installer (`Install.bat`). It needs no Python or admin
  rights, and creates desktop and Start menu shortcuts plus an "Apps & features" entry.
- **Easy updates** on Windows and Linux: running the installer of a newer version updates the
  app in place and keeps settings, routes, filters and models. Python packages are only
  reinstalled when they changed.
- Linux: the app now installs to `~/.local/share/vrc-voice-translator` with a menu entry and a
  `vrc-voice-translator` command, so the extracted folder can be deleted.
- Runs on the CPU when there's no NVIDIA GPU (uses the small model by default).
- Version shown in the window; app icon; crash log and error message if the app fails.
- Microphone selection works reliably on Windows (each mic is listed once).
- Uninstaller on both systems (optionally keeps settings and models).

## 1.3
- Translation routes: any number of "from → to" pairs at the same time
  (e.g. A → B and B → A at the same time), with 47 languages.
- One-click translator download with status display and install prompt.

## 1.2
- Translation into languages other than English (NLLB-200), chatbox formats, transcript saving.

## 1.1
- Filters: word replacements, blocked phrases, filler-word removal, minimum sentence length.

## 1.0
- First version: speech to VRChat chatbox with a GUI.
