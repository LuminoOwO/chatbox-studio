CHATBOX STUDIO - WINDOWS
========================

Speak into your mic and your VRChat chatbox shows it - as you said it or
translated into any of 47 languages. Everything runs on your own PC, nothing
you say is sent to the internet.


INSTALL (about 5 minutes)
-------------------------
1. Double-click "Chatbox-Studio-X.Y-Windows-Setup.exe" and click Install.
   - If Windows shows "Windows protected your PC", click "More info" and then
     "Run anyway" (the setup isn't signed with a paid certificate).
   - It downloads Python by itself. No admin rights needed.
   - If Windows blocks it completely ("Smart App Control"), use the ZIP
     instead: right-click it, "Extract All", then double-click Install.bat.
2. The "Chatbox Studio Setup" window opens. Pick one:
   - Standard (recommended): speech recognition and the translator, on your
     graphics card if you have one.
   - Speech only: smaller, no translator for other languages.
   - Custom...: every part yourself - speech model, translator, which
     graphics chip runs what (NVIDIA, AMD and Intel mixed), extras and the
     experimental Earshot.
   At the bottom: the download size and the graphics memory it needs (live,
   grey = what other programs use right now).

   Graphics:
   - NVIDIA: CUDA, fastest for everything.
   - AMD Radeon RX 6800 and newer, RX 7000, RX 9000: ROCm, needs the AMD
     Adrenalin driver 26.2.2 or newer.
   - Any other AMD card, AMD APUs (Radeon 680M / 780M / 890M), Intel Arc and
     Intel Iris Xe / UHD graphics: Vulkan - speech recognition on the chip
     with your normal driver. The translator then runs on the CPU.
   - No usable graphics: the CPU, with smaller models.
3. Click Install, wait, then Finish starts the app.
   In the app, "Graphics cards" on the left shows every card it uses, live:
   memory (Chatbox Studio / other programs / free), temperature and a graph
   of load and memory over the last 3 minutes.
4. Later, start it from the desktop shortcut or the Start menu.

To add or remove parts later: "Chatbox Studio Setup" in the Start menu,
"Modify" in Windows' Apps list, or Settings > Parts & about in
the app. Unticking something removes it again.

In VRChat: Action Menu > Options > OSC > Enabled.


USING IT
--------
- The first start shows a short Quick start and a tour of the window. Both
  come back via the Help button (top right, F1).
- Left: microphone and the things you change while playing, with a "Getting
  started" checklist on top. Settings (top right, Ctrl+,): theme, speech
  model, translator, VRChat ports, filters, parts.
- "Translation routes" (top right): each row means "when I speak THIS language,
  the chatbox gets THAT language". All routes that are switched on work at the
  same time. The default only writes down English; add your own languages
  with "+ Add route" or in the Quick start.
- Translating into any language other than English needs the translator model
  (about 650 MB). The app asks you to install it the first time; the status at
  the top must show "Translator ready".
- Settings > Speech recognition > "Device": your graphics chip, or "CPU".
  On the CPU or older laptop graphics, choose the "small" or "medium" model.


CHATBOX TAB (status, music, clock, ...)
---------------------------------------
- The preview looks like the real VRChat chatbox. You build it out of blocks:
  the list on the left has everything that can go in (speech, text, line
  breaks, time, countdown, music, AFK, play time, and every PC, VR and VRChat
  value), by category, with a search box and what each one reads right now.
- Drag a block into the chatbox where you want it (Enter adds it at the end).
  Click a block to change it, drag it to move it, drag it back to the list or
  press Delete to remove it. Ctrl+Z undoes.
- Text blocks are your own text. Drop a value onto one to put it inside, like
  "CPU: {cpu}%". It only shows while its values have something.
- Priority: lower number = more important. Speech is 1, the rest 2. When the
  chatbox is full, the highest number makes room first (same numbers together).
- Scrolling: any block can show long text in a fixed window - ticker,
  marquee, bounce, lines, pages or typewriter.
- How much fits: click your speech block and change "Characters shown at once";
  the preview shows sample text of exactly that length. "Send sample to VRChat"
  shows it in game.
- Lyrics (experimental): shows the current line of the playing song. Uses .lrc
  files, or - only if you tick "Look up lyrics online" - the free LRCLIB
  database (only title, artist, album and length are sent).
- Privacy: the buttons under the title (Speech, Text, Time, Music, PC, VR, ...)
  hide those blocks right away. "Hide extras" hides everything except speech.
- Click a music block > "Filters and detected players...": hide websites or programs
  (e.g. "Website: youtube"), or only allow some. If something plays sound without
  song info (like a soundboard), the program name is shown instead.

EARSHOT (experimental)
----------------------
- The Earshot tab makes the app listen to the game as well: what the people
  around you say. "Room language" as a route target translates you into what
  the people around you speak, and it ignores your mic when it only hears the
  game through your speakers.
- What others say shows up in the Earshot tab, translated. It is never sent to
  your chatbox and nothing is saved.
- Its conversation features (hinting Whisper, smart translation with a local
  AI) are off by default - they often made results worse.
- On Windows it hears everything your PC plays. To hear only VRChat, set VRChat's
  output to a virtual cable (e.g. VB-CABLE) and pick that cable in Earshot.

APPEARANCE AND KEYBOARD
-----------------------
Settings (top right) > General: Dark, Light, High contrast or a colour scheme
(Catppuccin, Nord, Dracula, Gruvbox, Tokyo Night, Rose Pine), and text size.
Ctrl+P pause/resume, Ctrl+H hide extras, Ctrl+L clear chatbox,
Ctrl+1 / Ctrl+2 switch tabs. Tab moves between all controls.

UPDATE
------
Run the new Chatbox-Studio-X.Y-Windows-Setup.exe (or extract the new ZIP and
double-click its "Install.bat"). Your settings, routes, filters and downloaded
models are kept.

Coming from "VRC Voice Translator" (the old name of this app)? Same thing:
run the new Install.bat. It moves everything over, replaces the old shortcuts
and the old entry in Windows Apps, and the app is called Chatbox Studio from
then on.


UNINSTALL
---------
Windows Settings > Apps > "Chatbox Studio" > Uninstall,
or run "Uninstall.bat" in %LOCALAPPDATA%\Chatbox Studio.


IF SOMETHING DOESN'T WORK
-------------------------
- The log at the bottom right of the app says what's happening.
- If the app doesn't open at all: run "Start with console.bat" in
  %LOCALAPPDATA%\Chatbox Studio - it shows the error.
  Errors are also saved to %LOCALAPPDATA%\Chatbox Studio\crash.log
- No text in VRChat: check that OSC is enabled in VRChat.
- Using VOR or another OSC router (or face tracking) that takes port 9001?
  Leave "Find VRChat by itself" on (Settings > VRChat & OSC): the app picks a
  free port and VRChat finds it by itself. Or add Chatbox Studio in your router
  (host 127.0.0.1, port e.g. 9111) and set "Listen on port" to the same number.
- Installer failed: just run Install.bat again, it continues where it stopped.
- "Smart App Control blocked a file": Windows 11 only allows programs it already
  knows. Some of the AI libraries the app needs are unsigned, so Smart App Control
  can block them. The only way around it is to switch it off:
  Windows Security > App & browser control > Smart App Control settings > Off.
  Windows can't switch it back on later without a reset; Microsoft Defender
  keeps protecting the PC either way. Then run Install.bat again.
