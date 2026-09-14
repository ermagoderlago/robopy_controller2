#pragma once
#include "Arduino.h"
#include "driver/pulse_cnt.h"

// =============================================================================
// Waveshare General Driver (ESP32) - Closed-Loop PCNT Motor Controller
// Features:
// 1. Hardware PCNT 4X Quadrature Decoding with 2000ns Digital Glitch Filter
// 2. Closed-Loop Velocity Control (Feedforward + PI @ 50 Hz)
// 3. Dynamic Short-Braking on TB6612FNG (IN1=H, IN2=H) to Eliminate Coasting Jerk
// 4. 500ms Safety Watchdog (SPEC-01 Compliance)
// =============================================================================

// --- PINOUT WAVESHARE GENERAL DRIVER ---
const int PIN_M1_PWM  = 25;
const int PIN_M1_DIR1 = 21;
const int PIN_M1_DIR2 = 17;
const int PIN_M1_ENCA = 35; // Input-only (AC2)
const int PIN_M1_ENCB = 34; // Input-only (AC1)

const int PIN_M2_PWM  = 26;
const int PIN_M2_DIR1 = 22;
const int PIN_M2_DIR2 = 23;
const int PIN_M2_ENCA = 27; // BC1 (bidirectional, pull-up support)
const int PIN_M2_ENCB = 16; // BC2 (bidirectional, pull-up support)

const int PIN_BATTERY = 33;

// ESP-IDF v5 Pulse Counter handles
static pcnt_unit_handle_t pcnt_unit_m1 = NULL;
static pcnt_unit_handle_t pcnt_unit_m2 = NULL;
static pcnt_channel_handle_t pcnt_chan_m1_a = NULL;
static pcnt_channel_handle_t pcnt_chan_m1_b = NULL;
static pcnt_channel_handle_t pcnt_chan_m2_a = NULL;
static pcnt_channel_handle_t pcnt_chan_m2_b = NULL;

static unsigned long last_telemetry_time = 0;
static unsigned long last_cmd_time = 0;
static unsigned long last_pid_time = 0;

// --- CLOSED-LOOP VELOCITY PID & BRAKE PARAMETERS ---
// Max speed: ~118 ticks per 20ms at 100% duty (~5900 ticks/sec at 12.0V)
const float MAX_TICKS_PER_20MS = 118.0f;
const float MAX_INTEG = 250.0f;
const float STICTION_PWM = 35.0f;

static bool closed_loop_enabled = true;
static float pid_kp = 3.20f;
static float pid_ki = 0.25f;
static float pid_kd = 0.04f;

// Target normalized speeds [-1.0, 1.0]
static float target_duty_L = 0.0f;
static float target_duty_R = 0.0f;

// Stopping timers for timed active short-brake pulse
static unsigned long stop_time_L = 0;
static unsigned long stop_time_R = 0;

// PID state accumulators
static float integ_L = 0.0f;
static float integ_R = 0.0f;
static float prev_err_L = 0.0f;
static float prev_err_R = 0.0f;

static int last_pid_ticks_m1 = 0;
static int last_pid_ticks_m2 = 0;

static int last_applied_pwm_m1 = 0;
static int last_applied_pwm_m2 = 0;
static int last_pin35 = 0;
static int last_pin34 = 0;
static unsigned long trans_35 = 0;
static unsigned long trans_34 = 0;

// --- LOW-LEVEL H-BRIDGE ACTUATION ---
void apply_motor_hardware(int motor_id, int pwm_signed) {
  if (motor_id == 1) last_applied_pwm_m1 = pwm_signed;
  else last_applied_pwm_m2 = pwm_signed;

  int pin_pwm  = (motor_id == 1) ? PIN_M1_PWM  : PIN_M2_PWM;
  gpio_num_t pin_dir1 = (motor_id == 1) ? (gpio_num_t)PIN_M1_DIR1 : (gpio_num_t)PIN_M2_DIR1;
  gpio_num_t pin_dir2 = (motor_id == 1) ? (gpio_num_t)PIN_M1_DIR2 : (gpio_num_t)PIN_M2_DIR2;

  if (pwm_signed > 0) {
    // Forward rotation
    gpio_set_level(pin_dir1, 1);
    gpio_set_level(pin_dir2, 0);
    ledcWrite(pin_pwm, constrain(pwm_signed, 0, 255));
  } else if (pwm_signed < 0) {
    // Reverse rotation
    gpio_set_level(pin_dir1, 0);
    gpio_set_level(pin_dir2, 1);
    ledcWrite(pin_pwm, constrain(-pwm_signed, 0, 255));
  } else {
    // ACTIVE SHORT BRAKE (TB6612FNG: IN1=HIGH, IN2=HIGH, PWM=255)
    // Both lower MOSFETs turn ON, dead-shorting motor coils through GND.
    // Back-EMF produces instant dynamic counter-torque: zero inertial coasting!
    gpio_set_level(pin_dir1, 1);
    gpio_set_level(pin_dir2, 1);
    ledcWrite(pin_pwm, 255);
  }
}

void set_motor_speeds(float left, float right) {
  target_duty_L = constrain(left, -1.0f, 1.0f);
  target_duty_R = constrain(right, -1.0f, 1.0f);

  if (!closed_loop_enabled) {
    // Open-loop fallback with active short brake on stop
    int pwm_L = (abs(target_duty_L) < 0.01f) ? 0 : (int)(target_duty_L * 255.0f);
    int pwm_R = (abs(target_duty_R) < 0.01f) ? 0 : (int)(target_duty_R * 255.0f);
    apply_motor_hardware(1, pwm_L);
    apply_motor_hardware(2, pwm_R);
  }
}

void run_pid_control() {
  int curr_ticks_m1 = 0;
  int curr_ticks_m2 = 0;
  pcnt_unit_get_count(pcnt_unit_m1, &curr_ticks_m1);
  pcnt_unit_get_count(pcnt_unit_m2, &curr_ticks_m2);

  int delta_m1 = (curr_ticks_m1 - last_pid_ticks_m1) * 2;
  int delta_m2 = curr_ticks_m2 - last_pid_ticks_m2;
  last_pid_ticks_m1 = curr_ticks_m1;
  last_pid_ticks_m2 = curr_ticks_m2;

  // --- MOTOR 1 (LEFT WHEEL) ---
  if (abs(target_duty_L) < 0.01f) {
    integ_L = 0.0f;
    prev_err_L = 0.0f;
    if (stop_time_L == 0) stop_time_L = millis();
    if (millis() - stop_time_L < 250) {
      apply_motor_hardware(1, 0); // 250ms Active Short Brake pulse
    } else {
      // Standstill low-power idle
      digitalWrite(PIN_M1_DIR1, LOW);
      digitalWrite(PIN_M1_DIR2, LOW);
      ledcWrite(PIN_M1_PWM, 0);
    }
  } else {
    stop_time_L = 0;
    float target_ticks_L = target_duty_L * MAX_TICKS_PER_20MS;
    float err_L = target_ticks_L - (float)delta_m1;
    integ_L = constrain(integ_L + err_L, -MAX_INTEG, MAX_INTEG);
    float deriv_L = err_L - prev_err_L;
    prev_err_L = err_L;

    float ff_pwm_L = target_duty_L * 255.0f;
    float stiction_L = (target_duty_L > 0.0f) ? STICTION_PWM : -STICTION_PWM;
    float fb_pwm_L = (pid_kp * err_L) + (pid_ki * integ_L) + (pid_kd * deriv_L);
    int pwm_out_L = (int)constrain(ff_pwm_L + fb_pwm_L + stiction_L, -255.0f, 255.0f);
    apply_motor_hardware(1, pwm_out_L);
  }

  // --- MOTOR 2 (RIGHT WHEEL) ---
  if (abs(target_duty_R) < 0.01f) {
    integ_R = 0.0f;
    prev_err_R = 0.0f;
    if (stop_time_R == 0) stop_time_R = millis();
    if (millis() - stop_time_R < 250) {
      apply_motor_hardware(2, 0); // 250ms Active Short Brake pulse
    } else {
      // Standstill low-power idle
      digitalWrite(PIN_M2_DIR1, LOW);
      digitalWrite(PIN_M2_DIR2, LOW);
      ledcWrite(PIN_M2_PWM, 0);
    }
  } else {
    stop_time_R = 0;
    float target_ticks_R = target_duty_R * MAX_TICKS_PER_20MS;
    float err_R = target_ticks_R - (float)delta_m2;
    integ_R = constrain(integ_R + err_R, -MAX_INTEG, MAX_INTEG);
    float deriv_R = err_R - prev_err_R;
    prev_err_R = err_R;

    float ff_pwm_R = target_duty_R * 255.0f;
    float stiction_R = (target_duty_R > 0.0f) ? STICTION_PWM : -STICTION_PWM;
    float fb_pwm_R = (pid_kp * err_R) + (pid_ki * integ_R) + (pid_kd * deriv_R);
    int pwm_out_R = (int)constrain(ff_pwm_R + fb_pwm_R + stiction_R, -255.0f, 255.0f);
    apply_motor_hardware(2, pwm_out_R);
  }
}

void setup_waveshare() {
  gpio_reset_pin((gpio_num_t)PIN_M1_DIR1);
  gpio_reset_pin((gpio_num_t)PIN_M1_DIR2);
  gpio_reset_pin((gpio_num_t)PIN_M2_DIR1);
  gpio_reset_pin((gpio_num_t)PIN_M2_DIR2);

  gpio_set_direction((gpio_num_t)PIN_M1_DIR1, GPIO_MODE_INPUT_OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_M1_DIR2, GPIO_MODE_INPUT_OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_M2_DIR1, GPIO_MODE_INPUT_OUTPUT);
  gpio_set_direction((gpio_num_t)PIN_M2_DIR2, GPIO_MODE_INPUT_OUTPUT);
  pinMode(PIN_M1_PWM, OUTPUT);
  pinMode(PIN_M2_PWM, OUTPUT);

  // Initial state: active short brake to prevent any startup drift
  gpio_set_level((gpio_num_t)PIN_M1_DIR1, 1);
  gpio_set_level((gpio_num_t)PIN_M1_DIR2, 1);
  digitalWrite(PIN_M1_PWM, LOW);
  gpio_set_level((gpio_num_t)PIN_M2_DIR1, 1);
  gpio_set_level((gpio_num_t)PIN_M2_DIR2, 1);
  digitalWrite(PIN_M2_PWM, LOW);

  // Setup LEDC PWM (5 kHz, 8-bit)
  ledcAttach(PIN_M1_PWM, 5000, 8);
  ledcAttach(PIN_M2_PWM, 5000, 8);
  ledcWrite(PIN_M1_PWM, 0);
  ledcWrite(PIN_M2_PWM, 0);

  // M1 encoder pins (GPIO 34 & 35 input-only)
  pinMode(PIN_M1_ENCA, INPUT);
  pinMode(PIN_M1_ENCB, INPUT);
  // M2 encoder pins (GPIO 27 & 16 with pull-up)
  pinMode(PIN_M2_ENCA, INPUT_PULLUP);
  pinMode(PIN_M2_ENCB, INPUT_PULLUP);

  // --- PCNT UNIT SETUP ---
  pcnt_unit_config_t unit_config = {};
  unit_config.low_limit = -30000;
  unit_config.high_limit = 30000;
  unit_config.intr_priority = 0;
  unit_config.flags.accum_count = 1;

  pcnt_new_unit(&unit_config, &pcnt_unit_m1);
  pcnt_new_unit(&unit_config, &pcnt_unit_m2);

  // Hardware glitch filter: 2000 ns (2.0 µs)
  pcnt_glitch_filter_config_t filter_config = {};
  filter_config.max_glitch_ns = 2000;
  pcnt_unit_set_glitch_filter(pcnt_unit_m1, &filter_config);
  pcnt_unit_set_glitch_filter(pcnt_unit_m2, &filter_config);

  // --- MOTOR 1 CHANNEL (GPIO 34 edge, GPIO 21 direction level) ---
  // Channel A (GPIO 35) is open/disconnected on the robot.
  // We use active Channel B (GPIO 34) with direction gating from PIN_M1_DIR1 (GPIO 21).
  // Multiplied by 2 in PID and telemetry to match 4X CPR.
  pcnt_chan_config_t chan_m1_a_cfg = {};
  chan_m1_a_cfg.edge_gpio_num = PIN_M1_ENCB;
  chan_m1_a_cfg.level_gpio_num = PIN_M1_DIR1;
  pcnt_new_channel(pcnt_unit_m1, &chan_m1_a_cfg, &pcnt_chan_m1_a);

  pcnt_channel_set_edge_action(pcnt_chan_m1_a, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
  pcnt_channel_set_level_action(pcnt_chan_m1_a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);

  // --- MOTOR 2 CHANNELS (GPIO 27 & 16) ---
  // Symmetrical 4X quadrature decoding: positive duty produces positive ticks
  pcnt_chan_config_t chan_m2_a_cfg = {};
  chan_m2_a_cfg.edge_gpio_num = PIN_M2_ENCA;
  chan_m2_a_cfg.level_gpio_num = PIN_M2_ENCB;
  pcnt_new_channel(pcnt_unit_m2, &chan_m2_a_cfg, &pcnt_chan_m2_a);

  pcnt_chan_config_t chan_m2_b_cfg = {};
  chan_m2_b_cfg.edge_gpio_num = PIN_M2_ENCB;
  chan_m2_b_cfg.level_gpio_num = PIN_M2_ENCA;
  pcnt_new_channel(pcnt_unit_m2, &chan_m2_b_cfg, &pcnt_chan_m2_b);

  pcnt_channel_set_edge_action(pcnt_chan_m2_a, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
  pcnt_channel_set_level_action(pcnt_chan_m2_a, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
  pcnt_channel_set_edge_action(pcnt_chan_m2_b, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
  pcnt_channel_set_level_action(pcnt_chan_m2_b, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);

  // Enable and start PCNT hardware counters
  pcnt_unit_enable(pcnt_unit_m1);
  pcnt_unit_clear_count(pcnt_unit_m1);
  pcnt_unit_start(pcnt_unit_m1);

  pcnt_unit_enable(pcnt_unit_m2);
  pcnt_unit_clear_count(pcnt_unit_m2);
  pcnt_unit_start(pcnt_unit_m2);

  Serial.begin(115200);
  Serial.setTimeout(10);
  last_cmd_time = millis();
  last_pid_time = millis();
}

void loop_waveshare() {
  unsigned long now = millis();

  int p35 = digitalRead(PIN_M1_ENCA);
  int p34 = digitalRead(PIN_M1_ENCB);
  if (p35 != last_pin35) { trans_35++; last_pin35 = p35; }
  if (p34 != last_pin34) { trans_34++; last_pin34 = p34; }

  // 1. Serial Command Reception
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();

    if (input.indexOf("\"T\":133") >= 0) {
      // Dynamic PID Configuration: {"T":133,"pid":1,"kp":3.2,"ki":0.22}
      int pid_idx = input.indexOf("\"pid\":");
      if (pid_idx >= 0) {
        closed_loop_enabled = (input.substring(pid_idx + 6, pid_idx + 7).toInt() == 1);
      }
      int kp_idx = input.indexOf("\"kp\":");
      if (kp_idx >= 0) {
        pid_kp = input.substring(kp_idx + 5, input.indexOf(",", kp_idx)).toFloat();
      }
      int ki_idx = input.indexOf("\"ki\":");
      if (ki_idx >= 0) {
        pid_ki = input.substring(ki_idx + 5, input.indexOf(",", ki_idx)).toFloat();
      }
    } else if (input.indexOf("\"T\":1,") >= 0 || input.indexOf("\"T\":1 ") >= 0) {
      int l_idx = input.indexOf("\"L\":");
      int r_idx = input.indexOf("\"R\":");
      if (l_idx >= 0 && r_idx >= 0) {
        float left = input.substring(l_idx + 4, input.indexOf(",", l_idx)).toFloat();
        float right = input.substring(r_idx + 4, input.indexOf("}", r_idx)).toFloat();
        set_motor_speeds(left, right);
        last_cmd_time = now;
      }
    }
  }

  // 2. 500ms safety watchdog (SPEC-01 compliance)
  if (now - last_cmd_time > 500) {
    set_motor_speeds(0.0f, 0.0f);
  }

  // 3. 50 Hz Inner Closed-Loop PID Execution (every 20 ms)
  if (closed_loop_enabled && (now - last_pid_time >= 20)) {
    last_pid_time = now;
    run_pid_control();
  }

  // 4. 20 Hz Telemetry Output (every 50 ms)
  if (now - last_telemetry_time >= 50) {
    last_telemetry_time = now;
    float raw_v = analogRead(PIN_BATTERY);
    float voltage_mv = raw_v * (3300.0f / 4095.0f) * 11.0f;

    int left_ticks = 0;
    int right_ticks = 0;
    pcnt_unit_get_count(pcnt_unit_m1, &left_ticks);
    pcnt_unit_get_count(pcnt_unit_m2, &right_ticks);

    Serial.print("{\"T\":1001,\"odl\":");
    Serial.print(left_ticks * 2);
    Serial.print(",\"odr\":");
    Serial.print(right_ticks);
    Serial.print(",\"v\":");
    Serial.print((int)voltage_mv);
    Serial.print(",\"pid\":");
    Serial.print(closed_loop_enabled ? 1 : 0);
    Serial.print(",\"pwml\":");
    Serial.print(last_applied_pwm_m1);
    Serial.print(",\"pwmr\":");
    Serial.print(last_applied_pwm_m2);
    Serial.print(",\"d1\":");
    Serial.print(gpio_get_level((gpio_num_t)PIN_M1_DIR1));
    Serial.print(",\"d2\":");
    Serial.print(gpio_get_level((gpio_num_t)PIN_M1_DIR2));
    Serial.print(",\"p35\":");
    Serial.print(p35);
    Serial.print(",\"p34\":");
    Serial.print(p34);
    Serial.print(",\"t35\":");
    Serial.print(trans_35);
    Serial.print(",\"t34\":");
    Serial.print(trans_34);
    Serial.println("}");
  }
}
