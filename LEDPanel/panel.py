#!/usr/bin/env python3
"""
panel.py - one menu for the Bangla notice board.

    pip install pyserial pillow uharfbuzz freetype-py
    python panel.py                 pick the port from a list
    python panel.py COM3            go straight to that port

Flash the sketch once; everything below is set from here.

    duration, from a preset list or your own number of minutes
    which mp3 plays at which point in the countdown
    which .txt file is the running notice and which is the 00:00 notice
    volume
    profiles: save a whole setup by name and apply it in one step

Device settings live in /config.txt on the ESP32 and survive power cuts.
Profiles and track names live in panel.json here on the PC.
"""
import sys, os, json, time, glob, subprocess, threading

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial missing.  Run:  pip install pyserial")

BAUD     = 115200
HERE     = os.path.dirname(os.path.abspath(__file__))
STORE_F  = os.path.join(HERE, "panel.json")
DATA_DIR = os.path.join(HERE, "data")

# name, seconds
PRESETS = [
    ("30 seconds", 30),
    ("15 minutes", 15 * 60),
    ("20 minutes", 20 * 60),
    ("30 minutes", 30 * 60),
    ("45 minutes", 45 * 60),
    ("1 hour",     60 * 60),
    ("3 hours",   180 * 60),
]

DEFAULT_STORE = {
    "names": {"1": "start", "2": "10 min warning",
              "3": "5 min warning", "4": "time up"},
    "profiles": {},
}


# ------------------------------------------------------------ store
def load_store():
    try:
        with open(STORE_F, encoding="utf-8") as fh:
            s = json.load(fh)
        s.setdefault("names", dict(DEFAULT_STORE["names"]))
        s.setdefault("profiles", {})
        return s
    except Exception:
        return json.loads(json.dumps(DEFAULT_STORE))


def save_store(store):
    with open(STORE_F, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False)


# ------------------------------------------------------------ formatting
def fmt(sec):
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h and (m or s):
        return f"{h}h {m:02d}m" if not s else f"{h}h {m:02d}m {s:02d}s"
    if h:
        return f"{h} hour" + ("s" if h > 1 else "")
    if m and s:
        return f"{m}m {s:02d}s"
    if m:
        return f"{m} min"
    return f"{s} sec"


def ask(prompt, default=None):
    suffix = f" [{default}]" if default is not None else ""
    try:
        got = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return got if got else (default if default is not None else "")


class Spinner:
    """Keeps the prompt alive while a device operation runs, so the tool
       never looks frozen."""
    def __init__(self, text):
        self.text, self.stop = text, False

    def __enter__(self):
        def run():
            i = 0
            while not self.stop:
                print(f"\r  {self.text} {'.' * (i % 4):<3}", end="", flush=True)
                i += 1
                time.sleep(0.25)
            print("\r" + " " * (len(self.text) + 8) + "\r", end="", flush=True)
        self.t = threading.Thread(target=run, daemon=True)
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop = True
        self.t.join(timeout=1)


# ------------------------------------------------------------ device link
class Panel:
    """Talks to the sketch. Device chatter is never echoed - the menu
       reports what happened in its own words."""

    def __init__(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=0.4)
        time.sleep(2.2)                      # the ESP32 reboots on open
        self.ser.reset_input_buffer()
        self.send("VERBOSE 0")               # stop the per-second flood

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def send(self, text, wait=0.35):
        """Fire a command and drain the reply. Returns the lines."""
        self.ser.reset_input_buffer()
        self.ser.write((text + "\n").encode())
        self.ser.flush()
        out, deadline = [], time.time() + wait
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").rstrip()
            if line:
                out.append(line)
                deadline = time.time() + 0.25
        return out

    def ok(self, lines):
        return any(l.startswith("OK") for l in lines)

    def read_config(self):
        """-> dict(dur, vol, cues[(at, track, lead, label)]) or None."""
        for attempt in range(3):
            lines = self.send("CFG", wait=0.8)
            cfg = {"dur": None, "vol": None, "cues": []}
            for ln in lines:
                p = ln.split()
                if len(p) >= 2 and p[0] == "DUR":
                    cfg["dur"] = int(p[1])
                elif len(p) >= 2 and p[0] == "VOL":
                    cfg["vol"] = int(p[1])
                elif len(p) >= 4 and p[0] == "CUE":
                    cfg["cues"].append((int(p[1]), int(p[2]), int(p[3]),
                                        p[4] if len(p) > 4 else "cue"))
            if cfg["dur"] is not None:
                return cfg
            time.sleep(0.3)
        return None

    def set_duration(self, sec):
        return self.ok(self.send(f"DUR {sec}")) and self.ok(self.send("SAVE"))

    def set_volume(self, v):
        return self.ok(self.send(f"VOL {v}")) and self.ok(self.send("SAVE"))

    def write_cues(self, cues):
        """Replaces the whole list. Only one announcement per time point -
           two cues at the same moment would talk over each other."""
        seen, clean = set(), []
        for at, track, lead, label in sorted(cues, key=lambda c: -c[0]):
            if at in seen:
                continue
            seen.add(at)
            clean.append((at, track, lead, label))
        self.send("CUECLR")
        for at, track, lead, label in clean:
            self.send(f"CUEADD {at} {track} {lead} {label}", wait=0.2)
        return self.ok(self.send("SAVE"))

    def play(self, track):
        self.send(f"PLAY {track}", wait=0.2)

    def restart(self):
        self.send("RESTART")

    def upload_bin(self, path, timeup):
        """Push one BN1 file. Returns (ok, message)."""
        try:
            payload = open(path, "rb").read()
        except OSError as e:
            return False, str(e)
        if payload[:3] != b"BN1":
            return False, "not a BN1 file"

        cmd = "UPLOADT" if timeup else "UPLOAD"
        self.ser.reset_input_buffer()
        self.ser.write(f"{cmd} {len(payload)}\n".encode())
        self.ser.flush()

        deadline, ready = time.time() + 6, False
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line == "READY":
                ready = True
                break
            if line.startswith("ERR"):
                return False, line
        if not ready:
            return False, f"the board never answered {cmd} - is it running the new sketch?"

        for i in range(0, len(payload), 256):
            self.ser.write(payload[i:i + 256])
            self.ser.flush()
            time.sleep(0.01)

        deadline = time.time() + 6
        while time.time() < deadline:
            line = self.ser.readline().decode(errors="replace").strip()
            if line.startswith("OK"):
                return True, f"{len(payload)} bytes"
            if line.startswith("ERR"):
                return False, line
        return False, "no confirmation from the board"


# ------------------------------------------------------------ bitmaps
def list_txt():
    out = []
    for p in sorted(glob.glob(os.path.join(HERE, "*.txt"))):
        n = os.path.basename(p)
        if n.lower() != "panel.json":
            out.append(n)
    return out


def first_line_of(txt):
    try:
        for ln in open(os.path.join(HERE, txt), encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                return ln[:40]
    except Exception:
        pass
    return "(empty)"


def build_bin(txt, outname):
    """Run txt2bin.py. Returns (path, detail) or (None, error)."""
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "txt2bin.py"), txt, "--name", outname],
        cwd=HERE, capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        return None, (msg[-1] if msg else "txt2bin failed")
    size = ""
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("render"):
            size = ln.split(":", 1)[1].strip()
    path = os.path.join(DATA_DIR, outname)
    if not os.path.exists(path):
        return None, "txt2bin produced no file"
    return path, size


# ------------------------------------------------------------ views
def show_status(dev, store):
    with Spinner("reading the board"):
        cfg = dev.read_config()
    if not cfg:
        print("\n  Could not read the settings. Is the board running the"
              "\n  current sketch? Re-flash it and try again.")
        return None
    names = store["names"]
    print(f"\n  countdown : {fmt(cfg['dur'])}")
    print(f"  volume    : {cfg['vol']} / 30")
    if not cfg["cues"]:
        print("  announcements: none")
    else:
        print("  announcements:")
        times = [c[0] for c in cfg["cues"]]
        for at, track, lead, label in sorted(cfg["cues"], key=lambda c: -c[0]):
            nice = names.get(str(track), f"track {track}")
            when = "at 00:00" if at == 0 else f"{fmt(at)} left"
            warn = ""
            if at > cfg["dur"]:
                warn = "  (skipped: later than the countdown)"
            elif times.count(at) > 1:
                warn = "  (CLASH: two clips at this moment)"
            print(f"     {when:<16} track {track} - {nice}{warn}")
    return cfg


def choose_duration(dev):
    print()
    for i, (name, sec) in enumerate(PRESETS, 1):
        print(f"   {i}. {name}")
    print(f"   {len(PRESETS) + 1}. custom (minutes)")
    c = ask("  choice")
    if not c or not c.isdigit():
        return
    n = int(c)
    if 1 <= n <= len(PRESETS):
        sec = PRESETS[n - 1][1]
    elif n == len(PRESETS) + 1:
        m = ask("  how many minutes")
        try:
            sec = int(round(float(m) * 60))
        except (TypeError, ValueError):
            print("  not a number")
            return
        if sec < 1:
            print("  must be at least 1 second")
            return
    else:
        return

    with Spinner("setting the timer"):
        good = dev.set_duration(sec)
    print(f"  countdown is now {fmt(sec)}" if good else "  the board refused that")

    if good:
        cfg = dev.read_config()
        if cfg:
            late = [c for c in cfg["cues"] if c[0] > sec]
            if late:
                print(f"  note: {len(late)} announcement(s) are set further out than")
                print("        the countdown, so they will be skipped. Menu 3 to fix.")


def edit_cues(dev, store):
    names = store["names"]
    while True:
        cfg = dev.read_config()
        if not cfg:
            print("  could not read the board")
            return
        cues = sorted(cfg["cues"], key=lambda c: -c[0])
        print(f"\n  announcements   (countdown is {fmt(cfg['dur'])})")
        if not cues:
            print("   (none)")
        for i, (at, track, lead, label) in enumerate(cues, 1):
            nice = names.get(str(track), f"track {track}")
            when = "at 00:00" if at == 0 else f"{fmt(at)} left"
            warn = "   SKIPPED - later than the countdown" if at > cfg["dur"] else ""
            print(f"   {i}. {when:<16} track {track} - {nice}   lead {lead} ms{warn}")
        print("\n   a) add   d) delete   c) clear all   l) lead time   b) back")
        c = (ask("  choice", "b") or "b").lower()

        if c == "b":
            return

        if c == "a":
            w = ask("  when? minutes before the end, or 0 for time-up")
            if w is None:
                continue
            try:
                at = int(round(float(w) * 60))
            except ValueError:
                print("  not a number")
                continue
            if at > cfg["dur"]:
                print(f"  that is beyond the {fmt(cfg['dur'])} countdown - it would never play")
                continue
            t = ask("  which track number (001.mp3 = 1)")
            if not t or not t.isdigit():
                continue
            clash = next((c for c in cues if c[0] == at), None)
            if clash:
                print(f"  there is already an announcement there (track {clash[1]})")
                if not (ask("  replace it? (y/n)", "y") or "").lower().startswith("y"):
                    continue
                cues = [c for c in cues if c[0] != at]
            lead = ask("  lead ms (start delay compensation)", "800")
            label = "".join(ch for ch in names.get(t, "cue") if ch.isalnum())[:12] or "cue"
            cues.append((at, int(t), int(lead), label))
            with Spinner("saving"):
                dev.write_cues(cues)

        elif c == "d":
            n = ask("  delete which number")
            if not n or not n.isdigit() or not (1 <= int(n) <= len(cues)):
                continue
            cues.pop(int(n) - 1)
            with Spinner("saving"):
                dev.write_cues(cues)

        elif c == "c":
            with Spinner("clearing"):
                dev.write_cues([])

        elif c == "l":
            print("\n  The DFPlayer takes 300-1500 ms to start a track. Each cue")
            print("  fires this many ms early so the sound lands on time.")
            lead = ask("  lead ms for all cues", "800")
            if not lead or not lead.isdigit():
                continue
            with Spinner("saving"):
                dev.write_cues([(a, t, int(lead), l) for a, t, _, l in cues])


def name_tracks(store):
    print("\n  Friendly names for the menus only - the DFPlayer knows numbers.")
    print("  001.mp3 is track 1, 002.mp3 is track 2, and so on.")
    print("  Enter - to clear one, blank to keep it.\n")
    for n in range(1, 9):
        cur = store["names"].get(str(n), "")
        got = ask(f"  track {n}", cur or "-")
        if got is None:
            return
        if got == "-":
            store["names"].pop(str(n), None)
        elif got:
            store["names"][str(n)] = got
    save_store(store)
    print("  saved")


def test_audio(dev, store):
    t = ask("\n  track number to play (blank to go back)")
    if not t or not t.isdigit():
        return
    nice = store["names"].get(t, "")
    dev.play(int(t))
    print(f"  playing track {t}" + (f" - {nice}" if nice else ""))


def set_notices(dev, store):
    files = list_txt()
    if not files:
        print("\n  No .txt files in this folder. Create one and try again.")
        return

    print("\n  Text files in this folder:")
    for i, f in enumerate(files, 1):
        print(f"   {i}. {f:<18} {first_line_of(f)}")
    print("   0. leave unchanged")

    def pick(what):
        n = ask(f"\n  which file is the {what}")
        if not n or not n.isdigit() or int(n) == 0:
            return None
        n = int(n)
        return files[n - 1] if 1 <= n <= len(files) else None

    running = pick("notice WHILE the timer runs")
    timeup  = pick("notice AT 00:00")
    if not running and not timeup:
        print("  nothing changed")
        return

    for txt, outname, istime, what in (
            (running, "notice.bin", False, "running notice"),
            (timeup,  "timeup.bin", True,  "00:00 notice")):
        if not txt:
            continue
        print()
        with Spinner(f"building {txt}"):
            path, detail = build_bin(txt, outname)
        if not path:
            print(f"  {what}: build FAILED - {detail}")
            continue
        print(f"  {what}: built from {txt}  ({detail})")
        with Spinner("uploading"):
            good, msg = dev.upload_bin(path, timeup=istime)
        print(f"  {what}: {'uploaded, ' + msg if good else 'upload FAILED - ' + msg}")
        if good:
            store.setdefault("last_notices", {})[
                "timeup" if istime else "running"] = txt
            save_store(store)

    print("\n  Open notice_preview.png / timeup_preview.png to check the Bangla.")


def set_volume(dev):
    v = ask("\n  volume 0-30")
    if not v or not v.isdigit():
        return
    v = max(0, min(30, int(v)))
    with Spinner("setting volume"):
        good = dev.set_volume(v)
    print(f"  volume is now {v}" if good else "  the board refused that")


# ------------------------------------------------------------ profiles
def profile_menu(dev, store):
    while True:
        profs = store["profiles"]
        print("\n  profiles")
        if not profs:
            print("   (none saved yet)")
        names = sorted(profs)
        for i, n in enumerate(names, 1):
            p = profs[n]
            print(f"   {i}. {n:<18} {fmt(p['dur'])}, "
                  f"{len(p.get('cues', []))} announcement(s)")
        print("\n   s) save the current setup as a profile")
        print("   a) apply a profile     d) delete    b) back")
        c = (ask("  choice", "b") or "b").lower()

        if c == "b":
            return

        if c == "s":
            cfg = dev.read_config()
            if not cfg:
                print("  could not read the board")
                continue
            name = ask("  name for this profile")
            if not name:
                continue
            store["profiles"][name] = {
                "dur": cfg["dur"],
                "vol": cfg["vol"],
                "cues": cfg["cues"],
                "notices": store.get("last_notices", {}),
            }
            save_store(store)
            print(f"  saved '{name}'")

        elif c == "a":
            n = ask("  apply which number")
            if not n or not n.isdigit() or not (1 <= int(n) <= len(names)):
                continue
            key = names[int(n) - 1]
            p = store["profiles"][key]
            print(f"\n  applying '{key}'")
            with Spinner("setting duration"):
                dev.set_duration(p["dur"])
            with Spinner("setting volume"):
                dev.set_volume(p.get("vol", 15))
            with Spinner("writing announcements"):
                dev.write_cues([tuple(c) for c in p.get("cues", [])])

            for slot, istime, outname, what in (
                    ("running", False, "notice.bin", "running notice"),
                    ("timeup",  True,  "timeup.bin", "00:00 notice")):
                txt = p.get("notices", {}).get(slot)
                if not txt or not os.path.exists(os.path.join(HERE, txt)):
                    continue
                with Spinner(f"building {txt}"):
                    path, detail = build_bin(txt, outname)
                if not path:
                    print(f"  {what}: build FAILED - {detail}")
                    continue
                with Spinner("uploading"):
                    good, msg = dev.upload_bin(path, timeup=istime)
                print(f"  {what}: {'uploaded from ' + txt if good else 'FAILED - ' + msg}")

            dev.restart()
            print(f"  '{key}' applied, countdown restarted")
            show_status(dev, store)

        elif c == "d":
            n = ask("  delete which number")
            if not n or not n.isdigit() or not (1 <= int(n) <= len(names)):
                continue
            key = names[int(n) - 1]
            del store["profiles"][key]
            save_store(store)
            print(f"  deleted '{key}'")


def seed_profiles(store):
    """First run: starting points. The board itself ships with NO
       announcements - a profile or menu 3 is the only way to set them."""
    if store["profiles"]:
        return
    store["profiles"] = {
        "quick test": {
            "dur": 30, "vol": 15,
            "cues": [[30, 1, 800, "start"], [10, 2, 800, "tenleft"],
                     [0, 4, 800, "timeup"]],
            "notices": {"running": "notice.txt", "timeup": "timeup.txt"},
        },
        "1 hour exam": {
            "dur": 3600, "vol": 20,
            "cues": [[3600, 1, 800, "start"], [600, 2, 800, "tenmin"],
                     [300, 3, 800, "fivemin"], [0, 4, 800, "timeup"]],
            "notices": {"running": "notice.txt", "timeup": "timeup.txt"},
        },
        "3 hour exam": {
            "dur": 10800, "vol": 20,
            "cues": [[10800, 1, 800, "start"], [600, 2, 800, "tenmin"],
                     [300, 3, 800, "fivemin"], [0, 4, 800, "timeup"]],
            "notices": {"running": "notice.txt", "timeup": "timeup.txt"},
        },
    }
    save_store(store)


# ------------------------------------------------------------ main
def pick_port():
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("No serial ports found. Is the board plugged in?")
    if len(ports) == 1:
        return ports[0].device
    print("Ports:")
    for i, p in enumerate(ports, 1):
        print(f"  {i}. {p.device}   {p.description}")
    n = ask("Which one", "1")
    try:
        return ports[int(n) - 1].device
    except Exception:
        sys.exit("bad choice")


MENU = """
  1. settings overview
  2. countdown duration
  3. announcements
  4. name the mp3 tracks
  5. test play a track
  6. the two notices
  7. volume
  8. profiles
  9. restart the countdown
  0. quit
"""


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else pick_port()
    store = load_store()
    seed_profiles(store)

    try:
        dev = Panel(port)
    except serial.SerialException as e:
        sys.exit(f"Could not open {port}.\n{e}\n\n"
                 f"Close the Arduino Serial Monitor and try again.")

    print(f"\nConnected on {port}.")
    show_status(dev, store)

    try:
        while True:
            print(MENU)
            c = ask("  choice")
            if c is None or c == "0":
                break
            elif c == "1":
                show_status(dev, store)
            elif c == "2":
                choose_duration(dev)
            elif c == "3":
                edit_cues(dev, store)
            elif c == "4":
                name_tracks(store)
            elif c == "5":
                test_audio(dev, store)
            elif c == "6":
                set_notices(dev, store)
            elif c == "7":
                set_volume(dev)
            elif c == "8":
                profile_menu(dev, store)
            elif c == "9":
                dev.restart()
                print("  countdown restarted")
            else:
                print("  no such option")
    finally:
        dev.close()
        print("\nClosed. The board keeps its settings.")


if __name__ == "__main__":
    main()
