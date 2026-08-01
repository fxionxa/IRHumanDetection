# Pan/tilt camera control (MPU side).
#
# Split of responsibility:
#   browser  - owns key state, emits an axis vector (-1..1) on every change
#   this     - forwards the vector to the MCU and keeps the watchdog fed
#   sketch   - integrates velocity -> angle at 50 Hz with an accel ramp
#
# Nothing here blocks: commands go out via Bridge.notify (fire-and-forget) from
# a dedicated thread, so inference or web traffic can never stall a servo stop.

import threading
import time

from arduino.app_utils import Bridge, Logger

logger = Logger("PanTilt")

# Bridge method names. These must match Bridge.provide_safe() in sketch.ino
# exactly -- a mismatched notify() fails silently.
_RPC_SET_VELOCITY = "pt_set_velocity"
_RPC_STOP = "pt_stop"
_RPC_CENTER = "pt_center"
_RPC_GET_STATE = "pt_get_state"

# The sketch stops if it hears nothing for 500 ms, so refresh well inside that.
_HEARTBEAT_HZ = 10.0
_HEARTBEAT_PERIOD = 1.0 / _HEARTBEAT_HZ

# After going idle, keep repeating the zero command briefly. A single dropped
# "stop" notify would otherwise leave the servo moving until the watchdog trips.
_IDLE_REPEATS = 5


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


class PanTilt:
    """Velocity-based pan/tilt control over the Bridge.

    Args:
        speed_scale: Global multiplier on the axis vector, 0..1. Lets the UI
            offer a speed slider without changing the firmware's max speed.
    """

    def __init__(self, speed_scale: float = 1.0):
        self._lock = threading.Lock()
        self._pan = 0.0   # -1..1, +1 = right
        self._tilt = 0.0  # -1..1, +1 = up
        self._speed_scale = _clamp(speed_scale, 0.0, 1.0)

        # The MCU boots centered and stopped, so (0, 0) is already true -- start
        # in sync to avoid a redundant notify before the Bridge is even up.
        self._last_sent: tuple[int, int] | None = (0, 0)
        self._idle_repeats = 0

        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    def start(self):
        """Start the command thread. Safe to call once; ignored afterwards."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="PanTiltSender", daemon=True
        )
        self._thread.start()
        logger.info("pan/tilt sender started")

    def stop(self):
        """Stop the servos and shut the command thread down."""
        self.set_axis(0.0, 0.0)
        self._stop_event.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            Bridge.call(_RPC_STOP, timeout=2)
        except Exception as e:
            logger.warning(f"could not confirm servo stop: {e}")

    # -------------------------------------------------------------- control

    def set_axis(self, pan: float, tilt: float):
        """Set the movement vector. Each axis is -1..1; 0,0 means stop."""
        with self._lock:
            self._pan = _clamp(float(pan), -1.0, 1.0)
            self._tilt = _clamp(float(tilt), -1.0, 1.0)
            if self._pan or self._tilt:
                self._idle_repeats = 0
            else:
                self._idle_repeats = _IDLE_REPEATS
        # Push the change out now rather than waiting for the next heartbeat.
        self._wake.set()

    def set_speed_scale(self, scale: float):
        """Scale all motion, 0..1."""
        with self._lock:
            self._speed_scale = _clamp(float(scale), 0.0, 1.0)
        self._wake.set()

    def center(self) -> dict:
        """Recenter both axes immediately (a blocking call, unlike motion)."""
        self.set_axis(0.0, 0.0)
        try:
            raw = Bridge.call(_RPC_CENTER, timeout=5)
            pan, tilt = (float(v) for v in str(raw).split(",")[:2])
            return {"pan": pan, "tilt": tilt}
        except Exception as e:
            logger.error(f"center failed: {e}")
            return {"error": str(e)}

    def state(self) -> dict:
        """Read the current angles and velocities back from the MCU."""
        try:
            raw = Bridge.call(_RPC_GET_STATE, timeout=5)
            pan, tilt, pan_vel, tilt_vel = (float(v) for v in str(raw).split(",")[:4])
            return {
                "pan": pan,
                "tilt": tilt,
                "pan_velocity": pan_vel,
                "tilt_velocity": tilt_vel,
            }
        except Exception as e:
            logger.error(f"state read failed: {e}")
            return {"error": str(e)}

    # ---------------------------------------------------------------- inner

    def _run(self):
        while not self._stop_event.is_set():
            with self._lock:
                scale = self._speed_scale
                pan_pct = int(round(self._pan * scale * 100))
                tilt_pct = int(round(self._tilt * scale * 100))
                moving = bool(pan_pct or tilt_pct)
                if not moving and self._idle_repeats > 0:
                    self._idle_repeats -= 1
                    resend_idle = True
                else:
                    resend_idle = False

            payload = (pan_pct, tilt_pct)

            # While moving, resend every tick to feed the MCU watchdog. While
            # idle, send only on change plus a few repeats -- then go quiet and
            # let the watchdog hold the stop.
            if moving or resend_idle or payload != self._last_sent:
                try:
                    Bridge.notify(_RPC_SET_VELOCITY, pan_pct, tilt_pct)
                    self._last_sent = payload
                except Exception as e:
                    logger.warning(f"velocity notify failed: {e}")
                    self._last_sent = None  # force a resend next tick

            if moving:
                # Fixed cadence while moving; ignore any pending wake.
                self._wake.clear()
                time.sleep(_HEARTBEAT_PERIOD)
            else:
                # Idle: sleep until woken by input, but keep ticking while we
                # still owe idle repeats.
                if resend_idle:
                    self._wake.clear()
                    time.sleep(_HEARTBEAT_PERIOD)
                else:
                    self._wake.wait(timeout=1.0)
                    self._wake.clear()
