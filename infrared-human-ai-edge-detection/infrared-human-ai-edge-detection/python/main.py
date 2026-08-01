# Infrared Human AI Edge Detection
#
# IR day/night USB camera -> YOLOX object detection -> annotated stream in the
# browser, plus smooth keyboard/button pan-tilt control of the camera.
#
# The annotated video (bounding boxes + labels) is rendered by the model
# runner's own HTTP server on port 4912; the UI embeds it at /embed. This app
# serves the surrounding page and the control channel on port 7000.

import json
from datetime import datetime, UTC

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection

from pantilt import PanTilt

logger = Logger("IRDetect")

# --------------------------------------------------------------- model config
#
# To swap in a custom IR-trained model later, this block plus the `model:` key
# in app.yaml is the whole change -- nothing below depends on the model.
#
#   1. app.yaml:  arduino:video_object_detection: { model: <your-model-id> }
#   2. CLASSES_OF_INTEREST: the labels your model emits
#
DEFAULT_CONFIDENCE = 0.45

# YOLOX-nano is COCO-trained; these are the COCO labels we care about.
# Set to None to pass every class through unfiltered.
CLASSES_OF_INTEREST = {
    "person",
    "car",
    "truck",
    "bus",
    "motorcycle",
    "bicycle",
}

# Classes that should raise an alert in the UI, distinct from mere presence.
PRIORITY_CLASSES = {"person"}

# ------------------------------------------------------------------ instances

ui = WebUI()
pantilt = PanTilt()
detector = VideoObjectDetection(
    confidence=DEFAULT_CONFIDENCE,
    debounce_sec=0.0,  # stream every frame's detections; the UI throttles
)

# Local confidence floor, applied on top of whatever the model runner does.
# Not redundant: override_threshold() only works if the model exposes a tunable
# threshold (and only after the runner's first "hello"), so on models that
# don't, this is what actually makes the slider do something.
_confidence_floor = DEFAULT_CONFIDENCE


# ----------------------------------------------------------------- detections

def _relevant(detections: dict) -> dict:
    """Drop labels we don't care about and boxes below the local floor."""
    result = {}
    for label, boxes in detections.items():
        if CLASSES_OF_INTEREST is not None and label not in CLASSES_OF_INTEREST:
            continue
        kept = [b for b in boxes if b.get("confidence", 0.0) >= _confidence_floor]
        if kept:
            result[label] = kept
    return result


def on_detections(detections: dict):
    """Forward each frame's detections to the browser.

    `detections` is {label: [{"confidence": float,
                              "bounding_box_xyxy": (x1, y1, x2, y2)}, ...]}
    """
    wanted = _relevant(detections)
    if not wanted:
        return

    entries = []
    for label, boxes in wanted.items():
        for box in boxes:
            entries.append(
                {
                    "label": label,
                    "confidence": box.get("confidence", 0.0),
                    "box": list(box.get("bounding_box_xyxy", ())),
                    "priority": label in PRIORITY_CLASSES,
                }
            )

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "counts": {label: len(boxes) for label, boxes in wanted.items()},
        "detections": entries,
    }
    ui.send_message("detections", json.dumps(payload))


detector.on_detect_all(on_detections)


# -------------------------------------------------------------- UI -> control

def on_pantilt_input(sid, data):
    """Axis vector from the browser: {"pan": -1..1, "tilt": -1..1}.

    Sent on every keydown/keyup and on button press/release, so the browser
    stays the single source of truth for which controls are held.
    """
    try:
        pan = float(data.get("pan", 0.0))
        tilt = float(data.get("tilt", 0.0))
    except (AttributeError, TypeError, ValueError):
        logger.warning(f"malformed pantilt_input: {data!r}")
        return
    pantilt.set_axis(pan, tilt)


def on_pantilt_speed(sid, data):
    """Speed slider, 0..1."""
    try:
        scale = float(data if not isinstance(data, dict) else data.get("speed", 1.0))
    except (TypeError, ValueError):
        return
    pantilt.set_speed_scale(scale)


def on_pantilt_center(sid, data=None):
    return pantilt.center()


def on_override_threshold(sid, value):
    """Confidence slider, 0..1."""
    global _confidence_floor
    try:
        threshold = float(value if not isinstance(value, dict) else value.get("value"))
    except (TypeError, ValueError):
        return

    _confidence_floor = max(0.0, min(1.0, threshold))

    # Also push it into the model runner when supported -- filtering at the
    # source is cheaper than filtering here. Failure is expected on models
    # without a tunable threshold, so this is informational only.
    try:
        detector.override_threshold(threshold)
    except Exception as e:
        logger.debug(f"model-side threshold override unavailable ({e}); using local filter")


def on_ui_disconnect(sid):
    """Fail safe: a closed tab must not leave the camera panning."""
    logger.info(f"client {sid} disconnected - stopping servos")
    pantilt.set_axis(0.0, 0.0)


ui.on_message("pantilt_input", on_pantilt_input)
ui.on_message("pantilt_speed", on_pantilt_speed)
ui.on_message("pantilt_center", on_pantilt_center)
ui.on_message("override_th", on_override_threshold)
ui.on_disconnect(on_ui_disconnect)

# REST endpoints, handy for testing pan/tilt without the browser (see README).
ui.expose_api("GET", "/api/pantilt/state", lambda: pantilt.state())
ui.expose_api("GET", "/api/pantilt/center", lambda: pantilt.center())
ui.expose_api("GET", "/api/pantilt/stop", lambda: (pantilt.set_axis(0, 0), {"ok": True})[1])

pantilt.start()

App.run()
