# Infrared Human Detection on Arduino UNO Q

A night-capable human detector that runs entirely on the edge. A USB IR camera
feeds a YOLO object-detection model running on an **Arduino UNO Q**; the
annotated video is served to a browser, and the operator can pan and tilt the
camera smoothly from the keyboard or an on-screen D-pad.

The repo contains two things:

1. **Training** — scripts to convert the [LLVIP](https://bupt-ai-cz.github.io/LLVIP/)
   infrared pedestrian dataset from VOC to YOLO format and train a YOLO11n
   detector on it.
2. **Deployment** — a complete Arduino App Lab application (Python + `.ino`
   sketch + web UI) that runs the detector on-device and drives two servos.

Target platform: **Arduino UNO Q** (`arduino,imola`), App Lab CLI **0.12.1**,
bricks **0.11.0**.

---

## Repo layout

| Path                        | Purpose                                              |
| --------------------------- | ---------------------------------------------------- |
| `voc_to_yolo.ipynb`         | Converts LLVIP VOC XML annotations to YOLO `.txt`    |
| `data.yaml`                 | Dataset config passed to the Ultralytics trainer     |
| `app.yaml`                  | Brick manifest — model id and web UI                 |
| `python/main.py`            | Wires detection → UI and UI → servos                 |
| `python/pantilt.py`         | Velocity command sender + heartbeat thread           |
| `sketch/sketch.ino`         | 50 Hz velocity integrator, ramp, limits, watchdog    |
| `sketch/sketch.yaml`        | Build profile (`Servo 1.3.0`)                        |
| `assets/index.html`         | Page layout — video iframe, D-pad, readouts          |
| `assets/app.js`             | Key/button state, axis vector, detection rendering   |
| `assets/style.css`          | Dark theme                                           |

---

## Part 1 — Training the IR model

### Requirements

- Python 3.9+
- An NVIDIA GPU (verify with `nvidia-smi`); CPU training is possible but slow
- `pip install ultralytics`

### Prepare the dataset

1. Clone this repo:

   ```bash
   git clone https://github.com/fxionxa/IRHumanDetection.git
   cd IRHumanDetection
   ```

2. Download the LLVIP dataset from
   [Kaggle](https://www.kaggle.com/datasets/monishshrivastava1/llvip-dataset).

3. Delete the `Visible/` folder — this project trains on infrared only.

4. Rename `infrared/` to `images/`.

5. Rename `images/test/` to `images/val/`, so the splits match what Ultralytics
   expects. The final layout should be:

   ```
   LLVIP/
     images/
       train/
       val/
     labels/          # created in the next step
       train/
       val/
   ```

6. Open `voc_to_yolo.ipynb`, set `ROOT` to your `LLVIP` directory, and run it.
   This writes YOLO-format label files into `LLVIP/labels/`.

7. Confirm every image has a label — the counts must match pairwise:

   ```powershell
   dir LLVIP\images\train | Measure-Object
   dir LLVIP\labels\train | Measure-Object
   dir LLVIP\images\val   | Measure-Object
   dir LLVIP\labels\val   | Measure-Object
   ```

8. Edit `path` in `data.yaml` so it points at the directory containing `LLVIP`.

### Train

Smoke-test with a single epoch first — this also downloads the `yolo11n.pt`
weights:

```bash
yolo detect train model=yolo11n.pt data=data.yaml epochs=1 imgsz=640 batch=16 device=0
```

Then run the real thing:

```bash
yolo detect train model=yolo11n.pt data=data.yaml epochs=100 imgsz=640 batch=16 device=0
```

Reduce `batch` if you run out of VRAM.

### Evaluate and export

```bash
yolo detect predict model=runs/detect/train/weights/best.pt source=LLVIP/images/val
yolo export  model=runs/detect/train/weights/best.pt format=onnx
```

The exported model is what you deploy in Part 2.

---

## Part 2 — Running it on the UNO Q

### How it works

```
USB IR camera ──► video_object_detection brick ──► annotated MJPEG on :4912/embed
            │                                    │
            │ on_detect_all()                    │ <iframe>
            ▼                                    ▼
      python/main.py ──── web_ui brick ────► browser :7000
            │                                    │
            │ Bridge.notify("pt_set_velocity")   │ keydown/keyup
            ▼                                    │ axis vector
      sketch.ino (MCU) ◄──────────────────────────┘
            │
            ▼
      pan + tilt servos (D9 / D10)
```

**The bounding boxes are drawn by the model runner, not by this app.** The runner
serves its own annotated stream on port **4912**; the page embeds it at `/embed`
and renders the surrounding UI on port **7000**.

### Why motion is smooth

Discrete "move 5° per keypress" control feels steppy and is at the mercy of
network jitter. Instead:

| Where               | Responsibility                                                                             |
| ------------------- | ------------------------------------------------------------------------------------------ |
| Browser             | Owns key/button state; emits an axis vector `{pan: -1..1, tilt: -1..1}` **only on change** |
| `python/pantilt.py` | Forwards the vector to the MCU and heartbeats at 10 Hz                                     |
| `sketch/sketch.ino` | Integrates velocity → angle at 50 Hz with an acceleration ramp                             |

Because the MCU integrates, a late or dropped message changes the _speed_, never
causing a positional jump. Holding a key ramps up over ~0.25 s; releasing ramps
down. Diagonals work (W+D pans and tilts together).

**Fail-safes** — the servos stop if any of these happen:

- key released, or the D-pad button released
- browser tab loses focus or is hidden (`blur` / `visibilitychange`)
- WebSocket disconnects (`on_disconnect` → zero axis)
- MCU hears nothing for 500 ms (firmware watchdog ramps to a stop)

---

### Wiring and power

> ⚠️ **Two rails, one ground.** 40 kg-cm servos stall at several amps. Never
> power them from the UNO Q's 5 V pin, and never route servo current through a
> breadboard — the rails are typically rated ~1–2 A and will sag, browning out
> the board or melting the strips.

| Item                             | Connection                                                                  |
| -------------------------------- | --------------------------------------------------------------------------- |
| UNO Q                            | USB-C, from the powered hub                                                 |
| USB IR camera                    | USB-A on the powered hub                                                    |
| Servo supply                     | Separate 6.0–7.4 V DC supply, **≥ 5 A** (6 A+ if both servos move together) |
| Pan servo signal (orange/white)  | UNO Q **D9**                                                                |
| Tilt servo signal (orange/white) | UNO Q **D10**                                                               |
| Both servos V+ (red)             | Servo supply **+**                                                          |
| Both servos GND (brown/black)    | Servo supply **−**                                                          |
| **Common ground**                | Servo supply **−** ↔ UNO Q **GND**                                          |

The common ground is not optional — without it the PWM signal has no reference
and the servos will twitch or ignore commands.

Recommended extras:

- A **1000 µF+ electrolytic** across the servo supply near the servos, to absorb
  stall-current dips.
- Screw terminals or soldered joints for the servo power pair.
- A powered **USB-C hub** for the board + camera (the UNO Q alone may not supply
  enough for a 1080p camera).

Signal polarity note: hobby servo PWM is **active-high** — idle low, with a
0.5–2.5 ms high pulse every 20 ms. No pull-ups or inversion needed. (The onboard
RGB LEDs are active-low, but nothing here drives them.)

---

### Running it

```bash
arduino-app-cli app start ~/ArduinoApps/infrared-human-ai-edge-detection
arduino-app-cli app logs  ~/ArduinoApps/infrared-human-ai-edge-detection --follow
```

Then open `http://<board-ip>:7000`. First start takes ~30 s while the model
container comes up; the video panel shows a spinner until then.

Controls: `W` / `A` / `S` / `D` or the on-screen D-pad. The speed slider scales
the axis vector.

Stop with:

```bash
arduino-app-cli app stop ~/ArduinoApps/infrared-human-ai-edge-detection
```

> Only one App runs at a time — `app start` stops whatever was running.

---

### Deploying your trained model

The app ships with `yolox-object-detection` (YOLOX-nano, COCO) so it runs out of
the box. Swapping in the IR model from Part 1 is a two-line change — nothing in
the detection handling depends on the model:

1. `app.yaml` — change the model id:

   ```yaml
   bricks:
     - arduino:video_object_detection:
         model: <your-model-id>
   ```

2. `python/main.py` — update `CLASSES_OF_INTEREST` and `PRIORITY_CLASSES` to the
   labels your model emits (for the LLVIP model, just `person`).

Then clear the build cache and restart:

```bash
arduino-app-cli app clean-cache user:infrared-human-ai-edge-detection --force
arduino-app-cli app restart ~/ArduinoApps/infrared-human-ai-edge-detection
```

List the built-in models with `arduino-app-cli model list`.

> **Disk space:** the root partition is ~9.8 G and typically ~75 % full. Do **not**
> `pip install` PyTorch or similar on the board — the brick's model runner already
> provides inference. Check with `df -h /` before adding dependencies.

---

## Testing each part independently

Do these in order; each isolates one layer, so a failure tells you exactly where
the problem is.

### 1. Camera alone (no model, no servos)

```bash
ls -l /dev/video*                            # expect video0 (and video1 on some cameras)
v4l2-ctl --list-formats-ext -d /dev/video0   # if v4l-utils is installed
```

If `/dev/video0` is missing, it's a USB/hub/power problem — fix that before
anything else.

### 2. Detection pipeline (no servos)

Run the stock example, which uses the same brick and model runner:

```bash
arduino-app-cli app start /var/lib/arduino-app-cli/examples/video-generic-object-detection
arduino-app-cli app logs  /var/lib/arduino-app-cli/examples/video-generic-object-detection --follow
```

Open `http://<board-ip>:4912/embed` **directly** — you should see the annotated
feed with boxes. If that works but this app's video panel stays on the spinner,
the problem is the iframe/port, not the model.

### 3. Servos alone (no camera, no model)

With this app running, drive the servos over the REST endpoints — no browser, no
keyboard:

```bash
curl http://localhost:7000/api/pantilt/state    # {"pan":90.0,"tilt":90.0,...}
curl http://localhost:7000/api/pantilt/center   # recenter
curl http://localhost:7000/api/pantilt/stop     # emergency stop
```

Watch the MCU's own log while you do it:

```bash
arduino-app-cli monitor      # expect "pan/tilt ready - centered at 90,90"
```

If `state` returns an `error`, the Bridge isn't reaching the MCU — check that the
sketch built (see below) and that `Bridge.provide_safe` didn't log an error in
`arduino-app-cli monitor`.

### 4. Sketch compiles (without starting the app)

```bash
cp -r ~/ArduinoApps/infrared-human-ai-edge-detection/sketch /tmp/pt && \
arduino-cli compile --fqbn arduino:zephyr:unoq /tmp/pt
```

### 5. Full integration

Open `http://<board-ip>:7000`, confirm the feed shows boxes, then hold `A` and
watch the **Pan** readout climb smoothly. Release — it should decelerate, not
stop dead.

---

## Tuning

Motion feel — `sketch/sketch.ino`, near the top:

| Constant                        | Default        | Effect                               |
| ------------------------------- | -------------- | ------------------------------------ |
| `MAX_SPEED_DPS`                 | `45`           | Degrees/second at full deflection    |
| `ACCEL_DPS2`                    | `180`          | Ramp rate; lower = softer start/stop |
| `PAN_MIN_DEG` / `PAN_MAX_DEG`   | `0` / `180`    | Pan travel limits                    |
| `TILT_MIN_DEG` / `TILT_MAX_DEG` | `0` / `180`    | Tilt travel limits                   |
| `SERVO_MIN_US` / `SERVO_MAX_US` | `500` / `2500` | Pulse range for full travel          |

**Narrow the travel limits first** if the bracket binds at an extreme — a 40 kg
servo grinding against a hard stop will strip its gears or cook the supply. The
firmware clamps position _and_ zeroes that axis's velocity at a limit, so holding
a key at the end stop is harmless.

If the servos buzz or jitter at the extremes, narrow `SERVO_MIN_US`/`SERVO_MAX_US`
toward `544`/`2400` — the Zephyr Servo backend is software PWM on a 4 µs tick and
does not clamp writes, so these constants are the only guard.

---
