/*
 * STAGE 5g - BANGLA TIMER + TWO NOTICES + AUDIO CUE TABLE
 *            RED timer, GREEN notice, DFPlayer Mini on UART2
 *            Countdown STOPS at 00:00 - no auto restart
 *
 * TWO NOTICES, both editable on the PC without recompiling
 *       notice.txt  ->  /notice.bin   scrolls while the timer runs
 *       timeup.txt  ->  /timeup.bin   shown once 00:00 is reached
 *   If /timeup.bin is missing the normal notice just keeps scrolling,
 *   so nothing breaks if you only ever upload one.
 *
 *       python txt2bin.py            builds both
 *       python send_notice.py COM3   uploads both
 *
 * SD CARD LAYOUT  (AUDIO_MODE 0 - root, fastest seek)
 *       001.mp3   start
 *       002.mp3   first warning
 *       003.mp3   second warning
 *       004.mp3   time up
 *   Files sit in the card ROOT, no folder. play() uses FAT order, so
 *   format the card fresh and copy them ONE AT A TIME in numeric order.
 *   Delete any hidden junk (macOS "._" files) or the order shifts.
 *
 * LATENCY
 *   The module needs 300-1500 ms to seek and start decoding. leadMs
 *   fires each cue that much early so the sound lands on time.
 *   To measure yours: watch the serial log for "audio: ..." and time
 *   the gap to actual sound. Put that number in leadMs.
 *
 * CONFIGURING IT
 *   Nothing here needs recompiling. Run the menu tool on the PC:
 *       python panel.py
 *   It sets the countdown, the audio cues, the volume, and which .txt
 *   files become the two notices, then writes them to the device.
 *   Config lives in /config.txt on LittleFS and survives power cuts.
 *
 * SERIAL COMMANDS (115200):
 *    LIST            list files on LittleFS
 *    UPLOAD <n>      then send n raw bytes of notice.bin
 *    UPLOADT <n>     then send n raw bytes of timeup.bin
 *    FORMAT          erase and re-create the filesystem
 *    SRC             print notice.txt if present
 *    RESTART         restart the countdown from the top
 *    CUES            print the cue table
 *    PLAY <n>        play track n now (for testing)
 */

#include <ESP32-HUB75-MatrixPanel-I2S-DMA.h>
#include <ESP32-VirtualMatrixPanel-I2S-DMA.h>
#include <LittleFS.h>
#include <HardwareSerial.h>
#include <DFRobotDFPlayerMini.h>
#include "bangla_digits.h"       // BANGLA_DIGIT[10][], BD_W, BD_H
#include "notice_fallback.h"     // FB_NOTICE[], FB_NOTICE_W, FB_NOTICE_H

// ================= CONFIG =================
// COUNTDOWN_SECONDS and DF_VOLUME are only DEFAULTS. Once /config.txt
// exists on the device they are overridden at boot - use panel.py.
#define COUNTDOWN_SECONDS  7200
#define SCROLL_MS          55

// ---- visual extras: set to 1 to bring them back ----
#define SHOW_DIVIDER       0     // the blue rule between the bands
#define SHOW_SECONDS_BAR   1     // the small bar that ticked under the timer
#define BLINK_COLON        1     // set 0 for a steady colon

// ---- colours (R, G, B  0-255) --------------------------------------
#define TIMER_R   255            // countdown digits: red
#define TIMER_G     0
#define TIMER_B     0
#define NOTICE_R    0            // normal notice: green
#define NOTICE_G  255
#define NOTICE_B    0
#define TIMEUP_R  255            // time-up notice: amber, reads as a change
#define TIMEUP_G  140
#define TIMEUP_B    0

#define NOTICE_GAP         14
#define NOTICE_FILE        "/notice.bin"
#define TIMEUP_FILE        "/timeup.bin"
#define NOTICE_TXT         "/notice.txt"
#define MAX_NOTICE_BYTES   60000

// ---- DFPlayer audio -------------------------------------------------
#define DF_RX_PIN       32       // ESP32 in  <- DFPlayer TX
#define DF_TX_PIN       18       // ESP32 out -> DFPlayer RX
#define DF_VOLUME       15       // 0-30
#define DF_USE_ACK      false    // most clones never send the ACK frame
#define DF_BOOT_MS      2500     // settle time after begin(); raise if 1st cue lags

#define AUDIO_MODE      0        // 0 = root (fastest), 1 = /mp3 folder, 2 = numbered folder
#define DF_FOLDER       1        // only used by AUDIO_MODE 2

// ================= RUNTIME CONFIG =================
// Everything here is loaded from /config.txt on LittleFS at boot, so it
// can be changed from the PC with panel.py - no recompiling. The values
// below are only the defaults used when no config file exists yet.
//
// at        seconds remaining when the cue should be HEARD
// track     file number on the card
// leadMs    module latency in ms; the cue fires this much earlier
// label     shown in the serial log only
#define MAX_CUES   10
#define CONFIG_FILE "/config.txt"

struct Cue {
  uint32_t at;
  uint8_t  track;
  uint16_t leadMs;
  char     label[13];
};

Cue      cues[MAX_CUES];
uint8_t  cueCount = 0;
bool     cueFired[MAX_CUES];

uint32_t cfgCountdown = COUNTDOWN_SECONDS;   // seconds
uint8_t  cfgVolume    = DF_VOLUME;           // 0-30

// The per-second countdown and the audio lines are OFF by default: they
// flood the port and make the PC tool's replies hard to read. Send
// VERBOSE 1 if you want the old chatty behaviour while debugging.
bool     verbose = false;

void addCue(uint32_t at, uint8_t track, uint16_t leadMs, const char *label) {
  if (cueCount >= MAX_CUES) { Serial.println("ERR cue table full"); return; }
  cues[cueCount].at     = at;
  cues[cueCount].track  = track;
  cues[cueCount].leadMs = leadMs;
  strncpy(cues[cueCount].label, label, sizeof(cues[0].label) - 1);
  cues[cueCount].label[sizeof(cues[0].label) - 1] = 0;
  cueCount++;
}

// There are deliberately NO built-in announcements. A compiled-in list
// would merge with whatever panel.py writes and you would hear two clips
// at once. The cue table starts empty and /config.txt is its only source.
// ===================================================

#define PANEL_RES_X 32
#define PANEL_RES_Y 32
#define PANEL_CHAIN 1

#define TIMER_Y     2
#define SECBAR_Y    11
#define DIVIDER_Y   13
#define NOTICE_Y0   14
#define NOTICE_Y1   31
#define NOTICE_BAND_H (NOTICE_Y1 - NOTICE_Y0 + 1)   // 18 rows; txt2bin --band

#define R1_PIN 25
#define G1_PIN 26
#define B1_PIN 27
#define R2_PIN 14
#define G2_PIN 12
#define B2_PIN 13
#define A_PIN  23
#define B_PIN  19
#define C_PIN  5
#define D_PIN  17
#define E_PIN  -1
#define LAT_PIN 4
#define OE_PIN  15
#define CLK_PIN 16

MatrixPanel_I2S_DMA *dma  = nullptr;
VirtualMatrixPanel  *disp = nullptr;

HardwareSerial      dfSerial(2);
DFRobotDFPlayerMini dfp;
bool dfOK = false;

// ---- TWO notices ----------------------------------------------------
// normal : heap copy of /notice.bin, or the compiled-in PROGMEM fallback
// timeup : heap copy of /timeup.bin, or absent (then normal keeps running)
uint8_t *noticeRam = nullptr;      // non-null => /notice.bin loaded
uint16_t noticeW = 0, noticeH = 0;
uint8_t *timeupRam = nullptr;      // non-null => /timeup.bin loaded
uint16_t timeupW = 0, timeupH = 0;
bool     fsMounted = false;

// whichever of the two loop() is drawing this frame
uint8_t       *actRam  = nullptr;
const uint8_t *actRom  = nullptr;
uint16_t       actW = 0, actH = 0;
int            actYoff = NOTICE_Y0;
bool           showingTimeUp = false;

inline uint8_t actPixel(int x, int y) {
  return actRam ? actRam[y * actW + x]
                : pgm_read_byte(&actRom[y * actW + x]);
}

inline bool haveTimeUp() { return timeupRam && timeupW && timeupH; }

// point the draw code at one of the two, centring it in the band
void selectNotice(bool timeup) {
  if (timeup) { actRam = timeupRam; actRom = nullptr; actW = timeupW; actH = timeupH; }
  else if (noticeRam) { actRam = noticeRam; actRom = nullptr; actW = noticeW; actH = noticeH; }
  else { actRam = nullptr; actRom = FB_NOTICE; actW = FB_NOTICE_W; actH = FB_NOTICE_H; }

  actYoff = (actH >= NOTICE_BAND_H) ? NOTICE_Y0
                                    : NOTICE_Y0 + (NOTICE_BAND_H - actH) / 2;
  showingTimeUp = timeup;
}

void useFallback() {
  if (noticeRam) { free(noticeRam); noticeRam = nullptr; }
  noticeW = FB_NOTICE_W;
  noticeH = FB_NOTICE_H;
  Serial.printf("notice: using COMPILED-IN fallback (%u x %u)\n", noticeW, noticeH);
}

void listFS() {
  if (!fsMounted) { Serial.println("FS not mounted"); return; }
  Serial.println("--- LittleFS contents ---");
  File root = LittleFS.open("/");
  File f = root.openNextFile();
  int n = 0;
  while (f) { Serial.printf("  %-20s %8u bytes\n", f.name(), (unsigned)f.size()); n++; f = root.openNextFile(); }
  if (!n) Serial.println("  (empty)");
  Serial.printf("  total %u / used %u bytes\n",
                (unsigned)LittleFS.totalBytes(), (unsigned)LittleFS.usedBytes());
  Serial.println("-------------------------");
}

// Loads a BN1 file into whichever buffer is passed in. On failure the
// buffer is left exactly as it was, so a bad upload never blanks a
// notice that was already working.
bool loadBin(const char *path, uint8_t **ram, uint16_t *w_out, uint16_t *h_out) {
  if (!fsMounted) return false;
  File f = LittleFS.open(path, "r");
  if (!f) { Serial.printf("%s: not present\n", path); return false; }

  uint8_t magic[4], hdr[4];
  if (f.read(magic, 4) != 4 || magic[0] != 'B' || magic[1] != 'N' || magic[2] != '1') {
    Serial.printf("%s: bad magic (not a BN1 file)\n", path); f.close(); return false;
  }
  if (f.read(hdr, 4) != 4) { f.close(); return false; }
  uint16_t w = hdr[0] | (hdr[1] << 8);
  uint16_t h = hdr[2] | (hdr[3] << 8);
  uint32_t need = (uint32_t)w * h;
  if (!need || need > MAX_NOTICE_BYTES) {
    Serial.printf("%s: bad size %ux%u\n", path, w, h); f.close(); return false;
  }
  uint8_t *buf = (uint8_t *)malloc(need);
  if (!buf) { Serial.printf("%s: malloc failed\n", path); f.close(); return false; }
  if ((uint32_t)f.read(buf, need) != need) {
    Serial.printf("%s: short read\n", path); free(buf); f.close(); return false;
  }
  f.close();
  if (*ram) free(*ram);
  *ram = buf; *w_out = w; *h_out = h;
  Serial.printf("%s: loaded (%u x %u, %u bytes)\n", path, w, h, (unsigned)need);
  if (h != NOTICE_BAND_H)
    Serial.printf("   note: %u rows in an %d-row band - rebuild with --band %d\n",
                  h, NOTICE_BAND_H, NOTICE_BAND_H);
  return true;
}

bool loadNoticeFromFS() { return loadBin(NOTICE_FILE, &noticeRam, &noticeW, &noticeH); }
bool loadTimeUpFromFS() { return loadBin(TIMEUP_FILE, &timeupRam, &timeupW, &timeupH); }

// ---------- /config.txt ----------
// Plain text, one directive per line, so it can be read with SRC-style
// commands and repaired by hand if it ever gets mangled:
//     DUR 7200
//     VOL 15
//     CUE <at> <track> <leadMs> <label>
void printConfig() {
  Serial.println("--- config ---");
  Serial.printf("DUR %lu\n", (unsigned long)cfgCountdown);
  Serial.printf("VOL %u\n", cfgVolume);
  Serial.printf("RUNNING %d\n", showingTimeUp ? 0 : 1);
  for (uint8_t i = 0; i < cueCount; i++)
    Serial.printf("CUE %lu %u %u %s\n", (unsigned long)cues[i].at,
                  cues[i].track, cues[i].leadMs, cues[i].label);
  Serial.println("--- end config ---");
}

bool saveConfig() {
  if (!fsMounted) { Serial.println("ERR fs not mounted"); return false; }
  File f = LittleFS.open(CONFIG_FILE, "w");
  if (!f) { Serial.println("ERR cannot write config"); return false; }
  f.printf("DUR %lu\n", (unsigned long)cfgCountdown);
  f.printf("VOL %u\n", cfgVolume);
  for (uint8_t i = 0; i < cueCount; i++)
    f.printf("CUE %lu %u %u %s\n", (unsigned long)cues[i].at,
             cues[i].track, cues[i].leadMs, cues[i].label);
  f.close();
  Serial.println("OK config saved");
  return true;
}

// returns false if there is no config file - caller keeps the defaults
bool loadConfig() {
  if (!fsMounted) return false;
  File f = LittleFS.open(CONFIG_FILE, "r");
  if (!f) { Serial.println("config: none, using compiled defaults"); return false; }

  uint8_t  newCount = 0;
  uint32_t newDur   = cfgCountdown;
  uint8_t  newVol   = cfgVolume;
  Cue      tmp[MAX_CUES];

  char line[80];
  while (f.available()) {
    size_t n = f.readBytesUntil('\n', line, sizeof(line) - 1);
    line[n] = 0;
    while (n && (line[n-1] == '\r' || line[n-1] == ' ')) line[--n] = 0;
    if (!n || line[0] == '#') continue;
    if (!strncmp(line, "DUR ", 4)) {
      newDur = strtoul(line + 4, nullptr, 10);
    } else if (!strncmp(line, "VOL ", 4)) {
      newVol = (uint8_t)strtoul(line + 4, nullptr, 10);
    } else if (!strncmp(line, "CUE ", 4) && newCount < MAX_CUES) {
      char lbl[13] = "cue";
      unsigned long at = 0; unsigned tr = 0, ld = 0;
      int got = sscanf(line + 4, "%lu %u %u %12s", &at, &tr, &ld, lbl);
      if (got >= 3) {
        tmp[newCount].at     = at;
        tmp[newCount].track  = (uint8_t)tr;
        tmp[newCount].leadMs = (uint16_t)ld;
        strncpy(tmp[newCount].label, lbl, sizeof(lbl) - 1);
        tmp[newCount].label[sizeof(lbl) - 1] = 0;
        newCount++;
      }
    }
  }
  f.close();

  if (newDur == 0) { Serial.println("config: DUR 0 rejected"); return false; }
  cfgCountdown = newDur;
  cfgVolume    = newVol > 30 ? 30 : newVol;
  if (newCount) { memcpy(cues, tmp, sizeof(Cue) * newCount); cueCount = newCount; }
  Serial.printf("config: loaded (DUR %lu, VOL %u, %u cues)\n",
                (unsigned long)cfgCountdown, cfgVolume, cueCount);
  return true;
}

void printSrc() {
  if (!fsMounted) return;
  File t = LittleFS.open(NOTICE_TXT, "r");
  if (!t) { Serial.println("notice.txt: not present"); return; }
  Serial.println("--- notice.txt ---");
  while (t.available()) Serial.write(t.read());
  Serial.println("\n------------------");
  t.close();
}

// ---------- audio ----------
void playTrack(uint8_t n) {
  if (!dfOK) return;
#if   AUDIO_MODE == 1
  dfp.playMp3Folder(n);            // /mp3/000n.mp3
#elif AUDIO_MODE == 2
  dfp.playFolder(DF_FOLDER, n);    // /01/00n.mp3
#else
  dfp.play(n);                     // root, FAT order - fastest seek
#endif
}

void initAudio() {
  dfSerial.begin(9600, SERIAL_8N1, DF_RX_PIN, DF_TX_PIN);
  delay(500);
  dfOK = dfp.begin(dfSerial, DF_USE_ACK, /*doReset=*/true);
  if (!dfOK) {
    Serial.println("DFPlayer NOT FOUND - running silent.");
    Serial.println("  check common GND, 5 V at the module pins, RX/TX not swapped");
    return;
  }
  dfp.setTimeOut(500);
  delay(DF_BOOT_MS);                 // chip is still indexing the card
  dfp.volume(cfgVolume);
  delay(150);
  int nfiles = dfp.readFileCounts();
  Serial.printf("DFPlayer OK - volume %u, files on card %d, audio mode %d\n",
                cfgVolume, nfiles, AUDIO_MODE);
  if (nfiles <= 0)
    Serial.println("  WARNING: SD card not readable - no audio will play");
}

void printCues() {
  Serial.println("--- audio cues ---");
  if (!cueCount) Serial.println("  (none)");
  for (uint8_t i = 0; i < cueCount; i++) {
    Serial.printf("  %-8s track %u  heard at %lu s  (fires %u ms early)\n",
                  cues[i].label, cues[i].track,
                  (unsigned long)cues[i].at, cues[i].leadMs);
    if (cues[i].at > cfgCountdown)
      Serial.println("           ^-- NEVER FIRES, past the start of the countdown");
  }
  Serial.println("------------------");
}

// ---------- drawing ----------
void drawBanglaDigit(int d, int x, int y, uint8_t iR, uint8_t iG, uint8_t iB) {
  if (d < 0 || d > 9) return;
  for (int r = 0; r < BD_H; r++)
    for (int c = 0; c < BD_W; c++) {
      uint8_t v = pgm_read_byte(&BANGLA_DIGIT[d][r * BD_W + c]);
      if (!v) continue;
      int px = x + c, py = y + r;
      if (px < 0 || px >= PANEL_RES_X || py < 0 || py >= PANEL_RES_Y) continue;
      disp->drawPixel(px, py, disp->color565((iR*v)/255, (iG*v)/255, (iB*v)/255));
    }
}

void drawColon(int x, int y, uint16_t col) {
  disp->drawPixel(x, y + 2, col);
  disp->drawPixel(x, y + 6, col);
}

// draws the ACTIVE notice at horizontal offset sx, clipped to the band
void drawNoticeAt(int sx, uint8_t iR, uint8_t iG, uint8_t iB) {
  if (!actW || !actH) return;
  int xStart = max(0, -sx);                          // clip in X before looping
  int xEnd   = min((int)actW, PANEL_RES_X - sx);
  if (xStart >= xEnd) return;
  for (int y = 0; y < actH; y++) {
    int py = actYoff + y;                            // centred in the band
    if (py < NOTICE_Y0) continue;
    if (py > NOTICE_Y1) break;
    for (int x = xStart; x < xEnd; x++) {
      uint8_t v = actPixel(x, y);
      if (!v) continue;
      disp->drawPixel(sx + x, py,
        disp->color565((iR*v)/255, (iG*v)/255, (iB*v)/255));
    }
  }
}

void drawTimer(uint32_t remain, bool colonOn, bool expired) {
  uint8_t hh = remain / 3600, mm = (remain % 3600) / 60, ss = remain % 60;
  uint8_t a1, a2, b1, b2;
  if (hh > 0) { a1 = hh/10; a2 = hh%10; b1 = mm/10; b2 = mm%10; }
  else        { a1 = mm/10; a2 = mm%10; b1 = ss/10; b2 = ss%10; }

  // single colour for the whole countdown, including 00:00
  uint8_t R = TIMER_R, G = TIMER_G, B = TIMER_B;
  (void)expired;
  uint16_t cCol = colonOn ? disp->color565(R, G, B) : 0;

  int x = 2, y = TIMER_Y;
  drawBanglaDigit(a1, x, y, R, G, B); x += BD_W + 1;
  drawBanglaDigit(a2, x, y, R, G, B); x += BD_W + 1;
  drawColon(x, y, cCol);              x += 2;
  drawBanglaDigit(b1, x, y, R, G, B); x += BD_W + 1;
  drawBanglaDigit(b2, x, y, R, G, B);

#if SHOW_SECONDS_BAR
  int len = (ss * PANEL_RES_X) / 60;
  if (len > 0) disp->drawFastHLine(0, SECBAR_Y, len, disp->color565(R/4 + 10, G/4, B/4));
#else
  (void)ss;
#endif
}

// ---------- serial upload: isTimeUp picks which file ----------
void doUpload(uint32_t n, bool isTimeUp) {
  if (!fsMounted) { Serial.println("ERR fs not mounted"); return; }
  if (n == 0 || n > MAX_NOTICE_BYTES + 8) { Serial.println("ERR bad size"); return; }
  uint8_t *buf = (uint8_t *)malloc(n);
  if (!buf) { Serial.println("ERR malloc"); return; }

  Serial.println("READY");
  uint32_t got = 0, t = millis();
  while (got < n && millis() - t < 10000) {
    int a = Serial.available();
    if (a > 0) { got += Serial.readBytes(buf + got, min((uint32_t)a, n - got)); t = millis(); }
  }
  if (got != n) { Serial.printf("ERR timeout, got %u of %u\n", (unsigned)got, (unsigned)n);
                  free(buf); return; }
  if (!(buf[0]=='B' && buf[1]=='N' && buf[2]=='1')) {
    Serial.println("ERR not a BN1 file"); free(buf); return;
  }
  const char *path = isTimeUp ? TIMEUP_FILE : NOTICE_FILE;
  File f = LittleFS.open(path, "w");
  if (!f) { Serial.println("ERR open for write"); free(buf); return; }
  f.write(buf, n); f.close(); free(buf);
  Serial.printf("OK wrote %u bytes to %s\n", (unsigned)n, path);

  if (isTimeUp) loadTimeUpFromFS();
  else if (!loadNoticeFromFS()) useFallback();
  selectNotice(showingTimeUp && haveTimeUp());   // refresh what is on screen
}

// ---------- state ----------
uint32_t t0, lastScrollMs;
int32_t  scrollX;
int      lastShownSec = -1;

void restartCountdown() {
  t0 = millis();
  for (uint8_t i = 0; i < cueCount; i++) cueFired[i] = false;
  lastShownSec = -1;
  selectNotice(false);            // back to the normal notice
  scrollX = PANEL_RES_X;
  Serial.println("countdown restarted");
}

void handleSerial() {
  static char line[64]; static uint8_t idx = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (!idx) continue;
      line[idx] = 0; idx = 0;
      if      (!strcmp(line, "LIST"))    listFS();
      else if (!strcmp(line, "CFG"))     printConfig();
      else if (!strncmp(line, "VERBOSE ", 8)) {
        verbose = (line[8] != '0');
        Serial.printf("OK verbose %d\n", verbose ? 1 : 0);
      }
      else if (!strcmp(line, "SAVE"))    saveConfig();
      else if (!strcmp(line, "LOAD"))    { loadConfig(); restartCountdown(); }
      else if (!strcmp(line, "CUECLR"))  { cueCount = 0; Serial.println("OK cues cleared"); }
      else if (!strncmp(line, "DUR ", 4)) {
        uint32_t v = strtoul(line + 4, nullptr, 10);
        if (!v) Serial.println("ERR duration must be > 0");
        else { cfgCountdown = v; Serial.printf("OK duration %lu s\n", (unsigned long)v);
               restartCountdown(); }
      }
      else if (!strncmp(line, "VOL ", 4)) {
        uint32_t v = strtoul(line + 4, nullptr, 10);
        if (v > 30) v = 30;
        cfgVolume = (uint8_t)v;
        if (dfOK) dfp.volume(cfgVolume);
        Serial.printf("OK volume %u\n", cfgVolume);
      }
      else if (!strncmp(line, "CUEADD ", 7)) {
        char lbl[13] = "cue";
        unsigned long at = 0; unsigned tr = 0, ld = 0;
        int got = sscanf(line + 7, "%lu %u %u %12s", &at, &tr, &ld, lbl);
        if (got < 3) Serial.println("ERR usage: CUEADD <at> <track> <leadMs> [label]");
        else { addCue(at, (uint8_t)tr, (uint16_t)ld, lbl);
               Serial.printf("OK cue %u added\n", cueCount); }
      }
      else if (!strcmp(line, "SRC"))     printSrc();
      else if (!strcmp(line, "CUES"))    printCues();
      else if (!strcmp(line, "RESTART")) restartCountdown();
      else if (!strcmp(line, "FORMAT"))  { LittleFS.format(); Serial.println("OK formatted"); listFS(); }
      else if (!strncmp(line, "PLAY ", 5)) {
        uint8_t n = (uint8_t)strtoul(line + 5, nullptr, 10);
        Serial.printf("playing track %u at %lu ms\n", n, (unsigned long)millis());
        playTrack(n);
      }
      // UPLOADT is checked first: "UPLOAD " would not match it, but
      // keeping this order makes the intent obvious
      else if (!strncmp(line, "UPLOADT ", 8)) doUpload(strtoul(line + 8, nullptr, 10), true);
      else if (!strncmp(line, "UPLOAD ",  7)) doUpload(strtoul(line + 7, nullptr, 10), false);
      else Serial.println("cmds: LIST SRC CUES CFG SAVE LOAD RESTART FORMAT | "
                    "DUR <s> VOL <n> CUEADD <at> <trk> <lead> [lbl] CUECLR | "
                    "PLAY <n> VERBOSE <0|1> UPLOAD <n> UPLOADT <n>");
      return;                                   // one command per loop pass
    } else if (idx < sizeof(line) - 1) line[idx++] = c;
  }
}

void setup() {
  Serial.begin(115200);
  delay(400);
  Serial.println("\n=== STAGE 5g: BANGLA TIMER + TWO NOTICES + AUDIO CUES ===");

  // NOTE: begin(false) - do NOT auto-format, or a bad mount erases your upload
  fsMounted = LittleFS.begin(false);
  if (!fsMounted) {
    Serial.println("LittleFS mount FAILED.");
    Serial.println("  -> partition scheme has no filesystem, or the partition");
    Serial.println("     was written as SPIFFS. Send FORMAT, then re-upload.");
    fsMounted = LittleFS.begin(true);           // explicit, and we told you
    if (fsMounted) Serial.println("  (formatted a fresh LittleFS - it is empty)");
  }
  loadConfig();               // the ONLY source of duration, volume and cues
  if (!cueCount)
    Serial.println("no announcements set - run panel.py on the PC");
  initAudio();                // uses cfgVolume
  for (uint8_t i = 0; i < cueCount; i++) cueFired[i] = false;
  printCues();

  listFS();
  printSrc();
  if (!loadNoticeFromFS()) useFallback();
  if (!loadTimeUpFromFS())
    Serial.println("timeup: absent - the normal notice will stay on at 00:00");
  selectNotice(false);

  HUB75_I2S_CFG::i2s_pins pins = {
    R1_PIN, G1_PIN, B1_PIN, R2_PIN, G2_PIN, B2_PIN,
    A_PIN,  B_PIN,  C_PIN,  D_PIN,  E_PIN, LAT_PIN, OE_PIN, CLK_PIN
  };
  HUB75_I2S_CFG mxconfig(PANEL_RES_X * 2, PANEL_RES_Y / 2, PANEL_CHAIN, pins);
  mxconfig.clkphase = false;
  mxconfig.i2sspeed = HUB75_I2S_CFG::HZ_10M;

  dma = new MatrixPanel_I2S_DMA(mxconfig);
  dma->begin();
  dma->setBrightness8(70);
  dma->clearScreen();

  disp = new VirtualMatrixPanel(*dma, 1, 1, PANEL_RES_X, PANEL_RES_Y);
  disp->setPhysicalPanelScanRate(FOUR_SCAN_32PX_HIGH);

  t0 = millis(); lastScrollMs = millis(); scrollX = PANEL_RES_X;
  Serial.println("Init OK. Type CFG for the current settings.");
}

void loop() {
  handleSerial();

  uint32_t now = millis();
  uint32_t sinceStart = now - t0;
  uint32_t totalMs = cfgCountdown * 1000UL;
  bool expired = (sinceStart >= totalMs);
  uint32_t msRemain = expired ? 0 : (totalMs - sinceStart);
  uint32_t remain = msRemain / 1000;
  bool colonOn = BLINK_COLON ? ((sinceStart % 1000) < 500) : true;

  // ---- audio cues: millisecond resolution so leadMs is meaningful ----
  // A cue set further out than the countdown itself (say 10 min on a
  // 30 s timer) would otherwise be "already reached" at t=0 and fire
  // immediately alongside the real one. Disarm those instead.
  for (uint8_t i = 0; i < cueCount; i++) {
    if (cueFired[i]) continue;
    if (cues[i].at > cfgCountdown) { cueFired[i] = true; continue; }
    if (msRemain <= cues[i].at * 1000UL + cues[i].leadMs) {
      cueFired[i] = true;
      playTrack(cues[i].track);
      if (verbose)
        Serial.printf("audio: %s (track %u) at %lu ms\n",
                      cues[i].label, cues[i].track, (unsigned long)now);
    }
  }

  // ---- swap to the time-up notice the moment the timer expires ----
  bool wantTimeUp = expired && haveTimeUp();
  if (wantTimeUp != showingTimeUp) {
    selectNotice(wantTimeUp);
    scrollX = PANEL_RES_X;                  // start the new text off-screen
    if (verbose) Serial.printf("notice: now showing %s\n", wantTimeUp ? "timeup" : "notice");
  }

  if (now - lastScrollMs >= SCROLL_MS) {
    lastScrollMs = now;
    scrollX--;
    if (scrollX < -(int32_t)(actW + NOTICE_GAP)) scrollX = PANEL_RES_X;
  }

  uint8_t nR = showingTimeUp ? TIMEUP_R : NOTICE_R;
  uint8_t nG = showingTimeUp ? TIMEUP_G : NOTICE_G;
  uint8_t nB = showingTimeUp ? TIMEUP_B : NOTICE_B;

  disp->fillScreen(0);
  drawTimer(remain, colonOn, expired);
#if SHOW_DIVIDER
  disp->drawFastHLine(0, DIVIDER_Y, PANEL_RES_X, disp->color565(0, 30, 60));
#endif
  drawNoticeAt(scrollX, nR, nG, nB);
  drawNoticeAt(scrollX + actW + NOTICE_GAP, nR, nG, nB);

  if ((int)remain != lastShownSec) {
    lastShownSec = remain;
    if (verbose)
      Serial.printf("%02u:%02u:%02u%s\n", remain/3600, (remain%3600)/60, remain%60,
                    expired ? "  <-- TIME UP" : "");
  }
  // countdown holds at 00:00 - no auto restart. Send RESTART to run it again.
}