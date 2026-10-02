DUAL NOTICE - what changed from your backup
===========================================

Your backup sketch is untouched apart from the notice logic. The panel
init, pin map, scan rate, brightness and the whole audio cue system are
byte-identical, so if the backup lit up, this will too.

WHAT IS NEW
  notice.txt  ->  data/notice.bin  ->  /notice.bin   scrolls while timing
  timeup.txt  ->  data/timeup.bin  ->  /timeup.bin   shown at 00:00

  The sketch holds both in RAM and swaps the moment the countdown
  expires, restarting the scroll so the new text enters from the right.
  If /timeup.bin is missing, nothing changes - the normal notice keeps
  scrolling at 00:00 exactly as before. Nothing can break by omitting it.

  Notice colour is now GREEN. The time-up notice is AMBER so the change
  of state reads at a glance. Timer is still RED.

  txt2bin.py now defaults to --band 18, matching NOTICE_Y0..NOTICE_Y1,
  so the text fills the band instead of leaving rows 27-31 dark. Your
  old bitmaps were 13 rows; both have been rebuilt at 18.


USING IT
  cd LEDPanel

  python txt2bin.py                  notice.txt -> data/notice.bin
  python txt2bin.py timeup.txt       timeup.txt -> data/timeup.bin

  Look at notice_preview.png and timeup_preview.png.
  Close the Arduino Serial Monitor, then:

  python send_notice.py COM3         uploads BOTH

  Just one of them:
  python send_notice.py COM3 --only notice
  python send_notice.py COM3 --only timeup

  A good run prints [notice], [timeup], [filesystem], "done - 2 of 2".


BOTH .txt FILES
  UTF-8 only. Notepad: File > Save As > Encoding: UTF-8.
  Lines starting with # are ignored.
  Several lines are joined with a dash and scroll as one line.


SERIAL COMMANDS (115200)
  LIST | SRC | CUES | RESTART | FORMAT | PLAY <n> | UPLOAD <n> | UPLOADT <n>

  RESTART puts the normal notice back and re-arms the audio cues.


IF THE PANEL STAYS DARK
  That is not this change - the drawing code did not move. Check in this
  order: Serial Monitor at 115200 for the "=== STAGE 5g ===" banner; then
  the common ground between the SMPS and the ESP32; then that you are on
  the IN side of the HUB75 connector. If the banner never appears, the
  sketch did not flash.


STILL OUTSTANDING FROM BEFORE
  Your last log showed "files on card -1" - the DFPlayer cannot read its
  SD card, so no audio will play whatever the code does. Unrelated to
  the notices.

  COUNTDOWN_SECONDS is 35 for testing. Set your real duration.
