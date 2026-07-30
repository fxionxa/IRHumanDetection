/*
 * Infrared Human AI Edge Detection - pan/tilt servo controller (MCU side)
 *
 * The Linux side (python/pantilt.py) sends *velocity* commands, not positions.
 * This sketch integrates that velocity into an angle at a fixed 50 Hz tick,
 * with an acceleration ramp, so motion feels continuous instead of stepped.
 *
 * Doing the integration here (rather than on the MPU) keeps the motion immune
 * to Bridge latency/jitter: a late command changes the *speed*, never causes a
 * positional jump. A watchdog stops the servos if commands stop arriving.
 */

#include <Servo.h>
#include <Arduino_RouterBridge.h>

// ---------------------------------------------------------------- pins
static const int PAN_PIN  = 9;
static const int TILT_PIN = 10;

// -------------------------------------------------------- pulse range
// The Zephyr Servo backend is software PWM on a 4 us tick / 20 ms frame and
// does NOT clamp writeMicroseconds(), so every write below is clamped here.
// 500-2500 us is the usual full-travel range for 40 kg-cm digital servos.
// Narrow these first if your servos buzz or strain at the extremes.
static const int SERVO_MIN_US = 500;
static const int SERVO_MAX_US = 2500;

// ------------------------------------------------- travel limits (deg)
// Full travel on both axes. Tighten if the bracket ever hits an end stop.
static const float PAN_MIN_DEG  = 0.0f;
static const float PAN_MAX_DEG  = 180.0f;
static const float TILT_MIN_DEG = 0.0f;
static const float TILT_MAX_DEG = 180.0f;
static const float PAN_CENTER   = 90.0f;
static const float TILT_CENTER  = 90.0f;

// ------------------------------------------------------------- motion
static const float    MAX_SPEED_DPS  = 45.0f;  // deg/s at full deflection
static const float    ACCEL_DPS2     = 180.0f; // deg/s^2 ramp (~0.25 s to full)
static const uint32_t TICK_MS        = 20;     // 50 Hz, matches the servo frame
static const uint32_t CMD_TIMEOUT_MS = 500;    // watchdog: coast to a stop

Servo panServo;
Servo tiltServo;

// Angles are float so sub-degree velocity integration accumulates properly;
// only the microsecond output is quantised.
static float panDeg  = PAN_CENTER;
static float tiltDeg = TILT_CENTER;

static float panVel  = 0.0f;  // current (ramped) deg/s
static float tiltVel = 0.0f;
static float panVelTarget  = 0.0f;  // requested deg/s
static float tiltVelTarget = 0.0f;

static uint32_t lastCmdMs  = 0;
static uint32_t lastTickMs = 0;

static inline float clampf(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

static int degToMicros(float deg) {
  float us = SERVO_MIN_US + (deg / 180.0f) * (SERVO_MAX_US - SERVO_MIN_US);
  if (us < SERVO_MIN_US) us = SERVO_MIN_US;
  if (us > SERVO_MAX_US) us = SERVO_MAX_US;
  return (int)(us + 0.5f);
}

static void writeServos() {
  panServo.writeMicroseconds(degToMicros(panDeg));
  tiltServo.writeMicroseconds(degToMicros(tiltDeg));
}

// Move `cur` toward `target` by at most maxDelta (a slew-rate limiter).
static float approach(float cur, float target, float maxDelta) {
  float diff = target - cur;
  if (diff >  maxDelta) return cur + maxDelta;
  if (diff < -maxDelta) return cur - maxDelta;
  return target;
}

// --------------------------------------------------------- Bridge API

// Velocity as a percentage of MAX_SPEED_DPS, -100..100 per axis.
// Integers keep the MsgPack typing unambiguous across the Bridge.
int pt_set_velocity(int panPct, int tiltPct) {
  panVelTarget  = clampf(panPct  / 100.0f, -1.0f, 1.0f) * MAX_SPEED_DPS;
  tiltVelTarget = clampf(tiltPct / 100.0f, -1.0f, 1.0f) * MAX_SPEED_DPS;
  lastCmdMs = millis();
  return 1;
}

// Soft stop: clears the request, the ramp brings speed to zero.
int pt_stop() {
  panVelTarget  = 0.0f;
  tiltVelTarget = 0.0f;
  lastCmdMs = millis();
  return 1;
}

String pt_center() {
  panVelTarget = tiltVelTarget = 0.0f;
  panVel = tiltVel = 0.0f;
  panDeg  = PAN_CENTER;
  tiltDeg = TILT_CENTER;
  lastCmdMs = millis();
  writeServos();
  return String(panDeg, 1) + "," + String(tiltDeg, 1);
}

String pt_get_state() {
  return String(panDeg, 1) + "," + String(tiltDeg, 1) + "," +
         String(panVel, 1) + "," + String(tiltVel, 1);
}

void setup() {
  Bridge.begin();
  Monitor.begin();

  // attach(pin, min, max) only affects write()/read() mapping on this core;
  // we drive writeMicroseconds() directly and clamp in degToMicros().
  panServo.attach(PAN_PIN, SERVO_MIN_US, SERVO_MAX_US);
  tiltServo.attach(TILT_PIN, SERVO_MIN_US, SERVO_MAX_US);
  writeServos();

  // provide_safe -> handlers run on the loop() thread via __loopHook, so they
  // never mutate the servo state concurrently with the integrator below.
  if (!Bridge.provide_safe("pt_set_velocity", pt_set_velocity))
    Monitor.println("ERR: could not provide pt_set_velocity");
  if (!Bridge.provide_safe("pt_stop", pt_stop))
    Monitor.println("ERR: could not provide pt_stop");
  if (!Bridge.provide_safe("pt_center", pt_center))
    Monitor.println("ERR: could not provide pt_center");
  if (!Bridge.provide_safe("pt_get_state", pt_get_state))
    Monitor.println("ERR: could not provide pt_get_state");

  lastCmdMs = lastTickMs = millis();
  Monitor.println("pan/tilt ready - centered at 90,90");
}

void loop() {
  // Must stay non-blocking: safe RPC handlers are dispatched between loop()
  // iterations, so a delay() here would stall incoming servo commands.
  uint32_t now = millis();
  if ((uint32_t)(now - lastTickMs) < TICK_MS) return;

  float dt = (now - lastTickMs) / 1000.0f;
  lastTickMs = now;

  // Watchdog: if the MPU goes quiet (lost notify, app stopped, browser closed)
  // ramp down instead of driving blindly.
  if ((uint32_t)(now - lastCmdMs) > CMD_TIMEOUT_MS) {
    panVelTarget = tiltVelTarget = 0.0f;
  }

  float maxDelta = ACCEL_DPS2 * dt;
  panVel  = approach(panVel,  panVelTarget,  maxDelta);
  tiltVel = approach(tiltVel, tiltVelTarget, maxDelta);

  panDeg  += panVel  * dt;
  tiltDeg += tiltVel * dt;

  // Clamp at the mechanical limits and kill the velocity on the axis that hit
  // it, so holding a key at an end stop doesn't wind up an invisible offset.
  if (panDeg <= PAN_MIN_DEG)   { panDeg  = PAN_MIN_DEG;  if (panVel  < 0) panVel  = 0; }
  if (panDeg >= PAN_MAX_DEG)   { panDeg  = PAN_MAX_DEG;  if (panVel  > 0) panVel  = 0; }
  if (tiltDeg <= TILT_MIN_DEG) { tiltDeg = TILT_MIN_DEG; if (tiltVel < 0) tiltVel = 0; }
  if (tiltDeg >= TILT_MAX_DEG) { tiltDeg = TILT_MAX_DEG; if (tiltVel > 0) tiltVel = 0; }

  writeServos();
}
