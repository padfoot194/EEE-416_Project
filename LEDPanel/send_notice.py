#!/usr/bin/env python3
"""
send_notice.py - push the notice bitmaps to the ESP32 over USB serial.

This replaces the Arduino LittleFS uploader plugin entirely. The sketch
listens for UPLOAD / UPLOADT and writes the bytes to LittleFS itself.

    pip install pyserial

    python send_notice.py COM3                 send BOTH bitmaps
    python send_notice.py COM3 --only notice   just the running notice
    python send_notice.py COM3 --only timeup   just the 00:00 notice

    python send_notice.py /dev/ttyUSB0             (Linux)
    python send_notice.py /dev/cu.usbserial-10     (macOS)

WHAT GOES WHERE

    data/notice.bin  --UPLOAD-->   /notice.bin   scrolls while timing
    data/timeup.bin  --UPLOADT-->  /timeup.bin   shown at 00:00

Build them first:
    python txt2bin.py                 notice.txt -> data/notice.bin
    python txt2bin.py timeup.txt      timeup.txt -> data/timeup.bin

Close the Arduino Serial Monitor before running this - it holds the port.
"could not open port ... Access is denied" always means another program
still has it open.
"""
import sys, time, os

try:
    import serial
except ImportError:
    sys.exit("pyserial missing.  Run:  pip install pyserial")

BAUD = 115200

# key, local file, serial command, destination on the ESP32
JOBS = [
    ("notice", os.path.join("data", "notice.bin"), "UPLOAD",  "/notice.bin"),
    ("timeup", os.path.join("data", "timeup.bin"), "UPLOADT", "/timeup.bin"),
]


def send_one(ser, path, cmd, dest):
    """Upload one BN1 file. Returns True on success."""
    payload = open(path, "rb").read()
    if payload[:3] != b"BN1":
        print(f"  {path} is not a BN1 file - skipped")
        return False

    w = payload[4] | payload[5] << 8
    h = payload[6] | payload[7] << 8
    print(f"  file    : {path}  ({len(payload)} bytes, {w} x {h})")
    print(f"  command : {cmd}  ->  {dest}")

    ser.reset_input_buffer()
    ser.write(f"{cmd} {len(payload)}\n".encode())
    ser.flush()

    deadline = time.time() + 6
    ready = False
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            print("  esp32:", line)
        if line == "READY":
            ready = True
            break
        if line.startswith("ERR"):
            print("  upload refused")
            return False
    if not ready:
        print(f"  no READY - is the sketch new enough to know {cmd}?")
        return False

    # small chunks so the ESP32's 256-byte UART FIFO never overflows
    CHUNK = 256
    for i in range(0, len(payload), CHUNK):
        ser.write(payload[i:i + CHUNK])
        ser.flush()
        time.sleep(0.01)

    ok = False
    deadline = time.time() + 6
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            print("  esp32:", line)
        if line.startswith("OK"):
            ok = True
            break
        if line.startswith("ERR"):
            break
    return ok


# ------------------------------------------------------------ arguments
args = sys.argv[1:]
only = None
if "--only" in args:
    i = args.index("--only")
    if i + 1 >= len(args):
        sys.exit("--only needs a name: notice or timeup")
    only = args[i + 1]
    if only not in [j[0] for j in JOBS]:
        sys.exit("--only must be 'notice' or 'timeup'")
    del args[i:i + 2]

PORT = args[0] if args else None
if not PORT:
    sys.exit(__doc__)

todo = [j for j in JOBS if only is None or j[0] == only]
if all(not os.path.exists(j[1]) for j in todo):
    sys.exit("no bitmaps to send - run:  python txt2bin.py")

print(f"port    : {PORT} @ {BAUD}")

try:
    ser = serial.Serial(PORT, BAUD, timeout=3)
except serial.SerialException as e:
    sys.exit(f"{e}\n\nClose the Arduino Serial Monitor (and any other program "
             f"using {PORT}), then try again.")

time.sleep(2.0)                       # ESP32 resets when the port opens

sent = 0
for key, path, cmd, dest in todo:
    print(f"\n[{key}]")
    if not os.path.exists(path):
        print(f"  {path} not found - skipped")
        if key == "timeup":
            print("  build it with:  python txt2bin.py timeup.txt")
        continue
    if send_one(ser, path, cmd, dest):
        sent += 1

print("\n[filesystem]")
ser.reset_input_buffer()
ser.write(b"LIST\n")
time.sleep(0.6)
while ser.in_waiting:
    print("  esp32:", ser.readline().decode(errors="replace").rstrip())
ser.close()

print(f"\ndone - {sent} of {len(todo)} uploaded.")
