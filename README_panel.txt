panel.py - the configuration menu
=================================

Flash Stage5c_Clean_speak.ino ONCE. Everything after that is set here.

    cd LEDPanel
    python panel.py              finds the port itself
    python panel.py COM3         or name it

Close the Arduino Serial Monitor first - it holds the port.


THE MENU
  1. settings overview      duration, volume, announcements
  2. countdown duration     30 sec, 15, 20, 30, 45 min, 1 hr, 3 hr, custom
  3. announcements          add, delete, clear, lead time
  4. name the mp3 tracks    friendly names for the menus
  5. test play a track      audition without waiting
  6. the two notices        pick .txt files, build and upload
  7. volume                 0-30
  8. profiles               save a whole setup, apply it in one step
  9. restart the countdown

The board's own chatter is never printed here. The menu says what
happened in its own words, and shows a spinner instead of freezing
while a command runs.


PROFILES
  A profile stores the duration, the volume, the announcement list and
  which .txt file is each notice. Applying one sets all of it and
  restarts the countdown.

  Three are created on first run so there is something to start from:
    quick test    30 s, three cues
    1 hour exam   3600 s, start / 10 min / 5 min / time up
    3 hour exam   10800 s, same cues

  Save your own with menu 8 -> s. They live in panel.json next to this
  script, so they survive reflashing the board.


THE BOARD HAS NO BUILT-IN ANNOUNCEMENTS
  The cue table starts empty. A compiled-in list would merge with
  whatever you set from the menu and you would hear two clips at once -
  that is exactly what "30 sec left / track 1" and "30 sec left /
  track 2" on the same line meant. /config.txt is now the only source.

  A freshly flashed board prints "no announcements set - run panel.py"
  and stays silent until you give it a cue list. The timer and both
  notices still work.

  The menu also refuses to put two announcements at the same moment: it
  offers to replace the existing one instead.


WHY AN ANNOUNCEMENT MIGHT BE "SKIPPED"
  A cue is placed by how much time is LEFT. A cue at 10 minutes left on
  a 30-second countdown was already past before the timer began, so it
  used to fire immediately alongside the start cue - two clips at once.

  The sketch now disarms any cue further out than the countdown, and the
  menu marks it SKIPPED so you can see why it is silent. Shorten the cue
  or lengthen the countdown.


WHERE SETTINGS LIVE
  On the ESP32: /config.txt on LittleFS, plain text, survives power cuts
  and reflashing:

      DUR 3600
      VOL 20
      CUE 600 2 800 tenmin
      CUE 300 3 800 fivemin
      CUE 0 4 800 timeup

  CUE is: <seconds remaining> <track> <lead ms> <label>
  Lead ms fires the cue early to hide the DFPlayer's start delay.

  On the PC: panel.json holds the profiles and the track names.

  No /config.txt means the sketch uses its compiled defaults, so a fresh
  board works before you ever run panel.py.


THE TWO NOTICES
  Any .txt file in this folder can be either notice. Menu 6 lists them
  with a preview of the first line and asks which is which, then builds
  and uploads both, reporting the pixel size of each.

      running notice  -> green, scrolls while the timer counts down
      00:00 notice    -> amber, replaces it when the timer ends

  Save as UTF-8. Lines starting with # are ignored. Several lines are
  joined with a dash and scroll as one line. Check the preview PNGs.


THE MP3 FILES
  Format the SD card FAT32 and copy the files into the ROOT, ONE AT A
  TIME, in numeric order - play() follows FAT write order, not filename:

      001.mp3  002.mp3  003.mp3  004.mp3

  Menu 5 is the fast way to confirm which number is which file. Boot
  prints "files on card N"; 0 or -1 means the module cannot read it.


IF YOU WANT THE CHATTY LOG BACK
  The sketch is quiet by default. Open a serial monitor at 115200 and
  send  VERBOSE 1  to get the per-second countdown and audio lines while
  debugging, VERBOSE 0 to stop. panel.py always sets it to 0 on connect.
