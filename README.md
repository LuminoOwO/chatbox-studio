# Chatbox Studio

Your VRChat chatbox, all in one app: speak and your words appear in the chatbox - as you said
them or translated into any of 47 languages - plus music, lyrics, clocks, status, PC and VR
headset info. Speech recognition and translation run locally on your own PC; nothing you say
is sent to the internet.

```
mic → voice detection → Whisper speech recognition ─────────────────→ VRChat chatbox (OSC)
                             │  into English: Whisper translates by itself
                             └─ into other languages → NLLB-200 translator ─┘
```

Runs on **Windows** and **Linux**, with **NVIDIA** (CUDA), **AMD** (ROCm or Vulkan),
**Intel** and **integrated graphics / APUs** (Vulkan), or just the CPU.

## Download and install

Get the files from the [Releases page](https://github.com/LuminoOwO/chatbox-studio/releases).

### Windows 10 / 11

Run **`Chatbox-Studio-<version>-Windows-Setup.exe`** and click *Install*. It installs for your
Windows user (no admin rights) and downloads Python from python.org by itself.

- *"Windows protected your PC"*: click **More info → Run anyway** (the setup isn't signed with
  a paid certificate).
- If **Smart App Control** blocks the setup completely, use
  `Chatbox-Studio-<version>-Windows.zip` instead: right-click → *Extract All*, then double-click
  `Install.bat`.

### Linux

| Distribution | Package | Install |
|---|---|---|
| Debian, Ubuntu, Linux Mint, Pop!_OS, Zorin, elementary | `chatbox-studio_<version>_amd64.deb` | `sudo apt install ./chatbox-studio_<version>_amd64.deb` |
| Fedora, Nobara, openSUSE, RHEL / Alma / Rocky | `chatbox-studio-<version>-1.x86_64.rpm` | `sudo dnf install ./chatbox-studio-<version>-1.x86_64.rpm` (openSUSE: `sudo zypper install ./…rpm`) |
| Arch, Manjaro, EndeavourOS, CachyOS, Garuda | `chatbox-studio-<version>-1-x86_64.pkg.tar.zst` | `sudo pacman -U ./chatbox-studio-<version>-1-x86_64.pkg.tar.zst` (or build `packaging/arch/PKGBUILD`) |
| Any other | `Chatbox-Studio-<version>-Linux.zip` | extract, then `./install.sh` (no root needed) |

The packages install the app to `/opt/chatbox-studio` with a menu entry and the commands
`chatbox-studio` and `chatbox-studio-setup`. The first start prepares a per-user Python
environment in `~/.local/share/chatbox-studio` (about 100 MB, with a progress window) - if
your distribution's Python is too new or too old for the AI packages, it fetches a standalone
Python 3.12 for it. Update by installing the newer package; uninstall with your package
manager (`~/.local/share/chatbox-studio` keeps your models until you delete it).

The ZIP's `install.sh` puts everything into `~/.local/share/chatbox-studio` instead; on
Debian-based systems it installs the few system libraries it needs with `apt`. Elsewhere,
install them first: PortAudio, xcb-util-cursor, libpulse, pciutils and the Vulkan loader
(Fedora: `portaudio xcb-util-cursor pulseaudio-libs pciutils vulkan-loader`; Arch:
`portaudio xcb-util-cursor libpulse pciutils vulkan-icd-loader`). Use either the package or
the ZIP, not both.

### First start: Chatbox Studio Setup

The setup window asks what to install:

- **Standard (recommended):** speech recognition and the translator (NLLB 600M), on your
  graphics card if you have one. The speech model is chosen for your hardware.
- **Speech only:** smaller - no translator for other languages (add it later any time).
- **Keep what I have** (when updating): updates the app, keeps your parts.
- **Custom…:** every part yourself - speech model, translator, which graphics chip runs what,
  extras (now playing, VR headset values) and the experimental Earshot.

At the bottom it shows the download size, the disk space, and how much **graphics memory**
your choice needs on each card - live, next to what other programs (VRChat, your browser)
use right now. Open the setup while VRChat runs to see what's realistically left.

Open it again any time to add or remove parts: **Chatbox Studio Setup** in the menu,
`chatbox-studio-setup`, or *⚙ Settings › Parts & about*. *Fresh install…* reinstalls every
part from scratch; *Uninstall completely…* removes the app's files, Python, shortcuts and -
if you like - settings and models.

**In VRChat:** Action Menu → Options → OSC → **Enabled**.

## Graphics cards

| Your graphics | Engine | Notes |
|---|---|---|
| NVIDIA GeForce / RTX | CUDA (CTranslate2) | Fastest for everything. Pascal (GTX 10 series) needs a driver branch that still supports it. |
| AMD Radeon RX 6800 and newer, RX 7000, RX 9000 | ROCm (CTranslate2) | Speech, translator and smart AI on the card. Windows: AMD Adrenalin 26.2.2 or newer. Linux: AMD's ROCm 7.2 libraries (the setup helps on Debian / Ubuntu). |
| Any AMD Radeon, AMD APUs (Radeon 680M / 780M / 890M, Steam Deck-class), Intel Arc and Intel Iris Xe / UHD graphics | **Vulkan** (whisper.cpp) | Speech recognition on the chip with your normal graphics driver - nothing else to install. The translator then runs on the CPU. On Linux this is the default for AMD, since it works on every distribution. |
| No usable graphics | CPU | Works, with smaller models. |

- **Integrated graphics / APUs** share the system memory; the setup counts what they can
  use and suggests a lighter model. Older Intel HD / UHD laptop graphics are hardly faster
  than the CPU, so the setup doesn't pick them by itself (you still can).
- **AMD APUs with ROCm on Linux:** chips ROCm only runs as a close relative (Radeon 780M,
  680M, RX 6600/6700) get the right `HSA_OVERRIDE_GFX_VERSION` automatically. Vulkan is
  simpler and usually just as fast for speech.
- **Several graphics chips, any mix:** every model can run on its own device - speech
  recognition, the translator, Earshot's listening model and its smart AI - NVIDIA, AMD and
  Intel together. Models on AMD cards (ROCm) run in a small helper process, Vulkan models in
  whisper.cpp's own server; the app doesn't care where. Pick the devices in the setup
  (*… runs on*) or later in *⚙ Settings* and the Earshot tab.
- **Live usage:** the main window's *Graphics cards* section shows, for each chip in use,
  Chatbox Studio's memory (colour), other programs' (grey), the temperature and a graph of
  load and memory over the last 3 minutes. Hover a card for the list of programs.

Every result in the transcript shows how many milliseconds it took, so you can compare
devices directly. With two cards, keeping the one VRChat renders on free is usually best.

## Finding your way around

The first start opens a short **Quick start** (microphone, your languages, VRChat) and offers
a **tour** that points at each part of the window. Both come back via **❔ Help** (F1).

- **Main window, left:** what you change while playing - microphone, voice threshold, pause
  length, *Send to chatbox*, *Pause when muted*, graphics card usage. On top, a **Getting
  started** checklist shows what's still missing with a button to fix each.
- **⚙ Settings** (top right, Ctrl+,): everything else, one page per topic with a search box -
  General (theme, text size), Speech recognition, Translation, VRChat & OSC, Filters,
  Parts & about.
- **▸ Log** under the tabs shows the app's messages; the latest one is always visible.

## Translation routes

The panel at the top right decides what happens to your voice. Each route says "when I speak
**this** language, the chatbox gets **that** language". The default only writes down English
as you say it; add your own languages with **+ Add route** or in the Quick start.

| Route | What it does |
|---|---|
| A → A (same language) | Sent as you said it. |
| A → B | You speak A, the chatbox shows B. |
| A → B and B → A | The app detects which of the two you're speaking, sentence by sentence. |
| A → B and A → C | Both translations are sent together. |

- **All routes that are switched on work at the same time.** Whisper only listens for the
  languages on the left side of your routes, so short phrases aren't mistaken for another
  language - keep that list short.
- **Presets** has *Add the other direction of every route* and *Translate everything I say
  into English*. The **⇄** button adds the opposite direction, the checkbox turns a route off
  without deleting it.
- "Several translations" (⚙ Settings › Translation) sets how multiple translations are joined.

### The translator

- Routes **into English** work out of the box: Whisper translates to English by itself.
- Routes **into any other language** need the translator, Meta's NLLB-200 (Standard installs
  it; otherwise the app offers it as soon as you add such a route). It's a one-time ~650 MB
  download; the 1.3B version (~1.4 GB) translates better.
- The status at the top shows downloading → loading → **Translator ready**. Until then,
  those sentences go out untranslated with an orange note in the transcript.
- If the download doesn't work, `setup_translator.sh` (Linux) builds the same files from
  Meta's original model (bigger download, takes a few minutes).
- **Use translator for English too**: NLLB instead of Whisper for → English.
- **Translate typed messages**: typed text counts as the language of your first route.

NLLB-200 is licensed for non-commercial use (CC BY-NC 4.0), which covers personal use.

### Chatbox format

| Format | Chatbox shows |
|---|---|
| `{text}` | the translation |
| `{text} ({original})` | the translation, then what you said in brackets |
| `{original}\n{text}` | what you said, and the translation on a second line |
| `[{src}→{dst}] {text}` | the language codes in front, e.g. `[EN→XX] …` |

When nothing was translated (you spoke the chatbox language), formats with `{original}` fall
back to just the text, so you don't get it twice. **Save…** next to the text field writes
the session's transcript to a text file.

## Chatbox: status, music, clock and more

The **💬 Chatbox** tab is a preview that looks like the in-game chatbox (Noto Sans,
black-and-white emoji, the same text width and margins, at most 9 lines - measured by the
[vrc-live-caption](https://github.com/Harry-Jing/vrc-live-caption) project). You build it out
of small **blocks**:

- **The block list** on the left has everything that can go into the chatbox, sorted into
  categories with a search box, and shows what each value reads right now: speech, text and
  line breaks, time and date, countdown, music (title, artist, progress bar, lyrics, ...),
  status (AFK, play time, active app), and PC, VR and VRChat values - CPU, graphics card,
  RAM, drives, network, headset and controller batteries, tracking type, ...
- **Add** a block by dragging it into the chatbox (or press Enter / double-click to add it at
  the end). **↵ New line** starts a new line.
- **Click** a block to change it. **Drag** it to move it (or Alt+← / Alt+→). Drag it back to
  the list, press Delete, or click *Remove* to take it out. **Undo** (Ctrl+Z) brings it back.
- **Text blocks** are your own text. Drop a value *onto* a text block to put it inside, e.g.
  “CPU: {cpu}%”. A text block with values only shows while all of them have something, so it
  never shows “CPU: %”. A part in `[ ]` disappears on its own instead:
  `🎵 {title}[ — {artist}]` shows just the title when there's no artist.
- **Spacing:** two value blocks next to each other get a space between them; text blocks are
  joined exactly as you typed them. A line where every block is empty disappears.

*Edit view* also shows empty blocks (greyed out) and line breaks so you can grab them; turn it
off to see exactly what VRChat shows. The counter shows how many of the 144 characters are
used - VRChat counts an emoji as two.

**Priority** is a number on every block. Lower = more important. Speech is 1 and everything
else 2 by default. When the chatbox is full, the highest number makes room first - all blocks
with that number together - then the next. While you talk, only blocks with a higher number
than speech make room. 0 = never removed.

**Block options** appear when a block needs them: a time zone and 12/24-hour style for clock
values (add a second clock for another time zone), the date and name for a countdown, title
length and filters for music, the lyrics source, the GPU number for graphics values.

**Scrolling:** every block can show long text in a fixed window: *Ticker*, *Marquee*,
*Bounce*, *Lines*, *Pages* and *Typewriter*. Speech has Pages, Scroll, Marquee and Typewriter.
Click your speech block and change *Characters shown at once* to see how much fits.

**Lyrics (experimental):** the lyrics blocks show the current line in time with the music,
from a `.lrc` file next to a local music file, a folder of `Artist - Title.lrc` files, or -
only if you tick *Look up lyrics online* - [LRCLIB](https://lrclib.net). For the online lookup
only title, artist, album and length are sent.

Values a system can't provide stay empty (CPU temperature isn't readable on Windows without
extra tools, RAM speed not on Linux without root). `{hostname}` is your computer's name and
the window title can show private things - think twice before showing them to strangers.

**VR values** are read from SteamVR only while it's running (the app connects as a background
app, so it never starts SteamVR). Quest users on Virtual Desktop or ALVR get headset and
controller values through SteamVR too. On **Linux**, **WiVRn** and **Monado** work without
SteamVR (headset name, refresh rate, bitrate, codecs, tracking features and batteries), and
the **Devices** category shows wireless device batteries (UPower, BlueZ, kernel drivers,
`headsetcontrol`). These Linux-only categories aren't shown on Windows.

**Privacy:** the bar under the title has a button per kind of block (Speech, Text, Time,
Music, Status, PC, VR, VRChat). Click one to hide all of those from VRChat right away, or
**🙈 Hide extras** to show only your speech. **Clear chatbox** removes the current message.

**Music filters** (click a music block → *Filters and detected players…*): "Show everything,
except" or "Only show", with rules such as *Website: youtube* or *Program: spotify*.
*Detected right now* lists every player and program making sound, with buttons to hide or
allow it. Programs that play sound without song info show their name instead (e.g.
"🔊 Soundpad"); VRChat, Discord and SteamVR are never shown by default. The website is read
from the browser's window title (X11 and Windows; not on pure Wayland).

VRChat takes about one chatbox update per second; *Update at most every* (default 1.2 s) sets
how often the app sends.

## 🎧 Earshot (experimental)

Normally the app only hears you. **Earshot** also listens to the game - what the people around
you say - and keeps a short memory of the conversation. Turn it on in the **🎧 Earshot** tab
(and tick it in the setup's Custom page). It then:

- **answers in the room's language** - pick **🎧 Room language** as the target of a route and
  you're translated into whatever the people around you speak;
- **ignores speaker echo** - sentences that are just what the game played aren't sent;
- **shows what others say**, translated into your language, in the Earshot tab (never in your
  chatbox);
- can run **its own listening model** on another device (e.g. an iGPU), so it hears the game
  while you talk.

Its conversation features - hinting Whisper with your recent sentences and *Smart
translation* with a local AI model (Qwen2.5 3B / 7B) - are **off by default**: in practice
they often made results worse. You can still switch them on in the Earshot tab.

**What it listens to:** Linux: VRChat only (PipeWire), everything you hear, or any input
device. Windows: everything you hear (loopback) or an input device; for VRChat only, route
VRChat to a virtual cable and pick that cable.

Everything stays on your PC, nothing is saved unless you press *Save…*, and what other players
say is never sent to the chatbox.

## Appearance and keyboard

*⚙ Settings › General*: **Dark**, **Light**, **High contrast**, **Catppuccin** (Mocha,
Macchiato, Frappé, Latte), **Nord**, **Dracula**, **Gruvbox**, **Tokyo Night** and **Rosé
Pine**, plus a text size from Small to Largest. Every control can be reached with Tab.

| Keys | Does |
|---|---|
| Ctrl+P | Pause / resume listening |
| Ctrl+H | Hide extras (privacy) |
| Ctrl+L | Clear the chatbox in VRChat |
| Ctrl+1 / 2 / 3 | Speech & translation / Chatbox / Earshot tab |
| Ctrl+, | Settings |
| F1 | Help |

## Making it faster

| Setting | Effect |
|---|---|
| **Send after pause** | The biggest lever. 500 ms is a good default; 300–400 ms feels snappier but can cut sentences in half. |
| **Decoding: Fastest** | Greedy decoding (default). "Most accurate" is noticeably slower on smaller cards. |
| **Routes from one language only** | Skips language detection. |
| **Chatbox format without `{original}`** | With `{original}`, Whisper writes the text twice when translating into English. |
| **Model: medium / small** | Two to four times as fast as large-v3, with rougher results. |
| **A faster device** | Compare the milliseconds in the transcript. |

Also: **Pause when muted in VRChat** stops transcribing while you're muted, a typing indicator
shows in VRChat while you speak, long sentences are split into 144-character messages, and
common Whisper hallucinations on silence ("Thanks for watching", subtitle credits in many
languages) are filtered out.

## Filters

*⚙ Settings › Filters* → **Edit filters…**:

- **Replace words**: e.g. `VR chat → VRChat`. Matching ignores case and keeps the original
  capitalisation; "Whole word" only matches whole words; leave "Replace with" empty to delete
  a word; tick "Regex" for patterns like `(\d+) euros? → €\1`.
- **Block sentences**: a sentence containing any of these is not sent (it's shown in the log).
- **Options**: remove filler words, ignore sentences shorter than N words, also filter typed text.

The "Try it" box shows exactly what would be sent. Filters run on the text that is sent, so
write your rules in the language of the output.

## Where things are

| | Linux | Windows |
|---|---|---|
| Settings | `~/.config/chatbox-studio/settings.json` | `%APPDATA%\Chatbox Studio` |
| Models, Python, logs | `~/.local/share/chatbox-studio` | `%LOCALAPPDATA%\Chatbox Studio` |
| Whisper models (CUDA / ROCm) | `~/.cache/huggingface` | `%USERPROFILE%\.cache\huggingface` |
| Crash log, start log | `crash.log`, `starts.log` in the data folder | the same |

## Troubleshooting

- **Using VOR or another OSC router** on port 9001: leave *Find VRChat with OSCQuery* on
  (⚙ Settings › VRChat & OSC) - the app then listens on a free port and VRChat finds it. Or add
  Chatbox Studio to the router (127.0.0.1, e.g. port 9111) and set *Listen on port* to it.
- **"Mute sync: port 9001 busy"** (only with OSCQuery off): another OSC app uses that port.
- **No microphone audio (Linux)**: choose `pipewire` or `default` in the microphone list.
- **`libcublas.so.12` not found**: start the app from the menu or with `chatbox-studio`, not
  with `python app.py` - the launcher sets the library path.
- **The app doesn't open**: run `chatbox-studio` in a terminal to see the error; crashes are
  written to `crash.log`.
- **Vulkan: "whisper-server doesn't start"** (Linux): install the Vulkan loader and your
  driver's Vulkan support - Debian/Ubuntu `libvulkan1 mesa-vulkan-drivers`, Fedora
  `vulkan-loader mesa-vulkan-drivers`, Arch `vulkan-icd-loader vulkan-radeon` / `vulkan-intel`.
  Check with `vulkaninfo --summary`.
- **Sentences arrive untranslated**: the status at the top has to say **Translator ready**.
  Untranslated sentences get an orange note in the transcript saying why.
- **A model fails to load on an NVIDIA card**: check that `nvidia-smi` shows the card.

## Building from source

```bash
python3 build_release.py        # every package into dist/
```

Needs `makensis` (NSIS) for the Windows setup, `dpkg-deb` for the .deb, `rpmbuild` for the
.rpm and `bsdtar` + `zstd` for the Arch package; a missing tool skips its package. Pushing a
tag `v<version>` builds and publishes all of them (`.github/workflows/release.yml`). The
Vulkan speech engine is built by `.github/workflows/engines.yml`. See `RELEASING.md`.

## Credits

Built on [Whisper](https://github.com/openai/whisper) (MIT),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
[CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT),
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT),
[NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M) (CC BY-NC 4.0),
[Qwen2.5](https://huggingface.co/Qwen) (optional smart translation),
[PySide6 / Qt](https://doc.qt.io/qtforpython-6/) (LGPL) and the
[Noto](https://fonts.google.com/noto) fonts (OFL). Models are downloaded from Hugging Face on
your request; they keep their own licenses.
