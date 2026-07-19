#pragma once
#include "Arduino.h"

// --- PINOUT WAVESHARE GENERAL DRIVER ---
const int PIN_M1_PWM = 25;
const int PIN_M1_DIR1 = 21;
const int PIN_M1_DIR2 = 17;
const int PIN_M1_ENCA = 35; // Input-only
const int PIN_M1_ENCB = 34; // Input-only

const int PIN_M2_PWM = 26;
const int PIN_M2_DIR1 = 22;
const int PIN_M2_DIR2 = 23;
// Motor B connector (MB1/MB2) encoder: BC1=GPIO27, BC2=GPIO16
// GPIO 27 and 16 are bidirectional — they DO support INPUT_PULLUP and interrupts!
const int PIN_M2_ENCA = 27; // BC1 signal on Motor B 6P connector
const int PIN_M2_ENCB = 16; // BC2 signal on Motor B 6P connector

const int PIN_BATTERY = 33;

// We use static variables for interrupt handlers
volatile long left_ticks = 0;
volatile long right_ticks = 0;
unsigned long last_telemetry_time = 0;

// LEFT (Motor A, MA connector): direction-aware quadrature decode.
// Interrupt on GPIO 35 (AC2/C2). Read GPIO 34 (AC1/C1) for direction.
void IRAM_ATTR left_encoder_isr() {
  if (digitalRead(PIN_M1_ENCA) == digitalRead(PIN_M1_ENCB)) {
    left_ticks++;
  } else {
    left_ticks--;
  }
}

// RIGHT (Motor B, MB connector): direction-aware quadrature decode.
// Interrupt on GPIO 16 (BC2/C2). Read GPIO 27 (BC1/C1) for direction.
// Standard quadrature logic: if C1 == C2 (both same level) → forward; else → backward.
void IRAM_ATTR right_encoder_isr() {
  if (digitalRead(PIN_M2_ENCA) == digitalRead(PIN_M2_ENCB)) {
    right_ticks++;
  } else {
    right_ticks--;
  }
}

void set_motor_speeds(float left, float right) {
  // Constrain input to -1.0 to 1.0 range
  left = constrain(left, -1.0f, 1.0f);
  right = constrain(right, -1.0f, 1.0f);

  int pwm_L = (int)(abs(left) * 255.0f);
  if (left > 0.01f) {
    digitalWrite(PIN_M1_DIR1, HIGH);
    digitalWrite(PIN_M1_DIR2, LOW);
  } else if (left < -0.01f) {
    digitalWrite(PIN_M1_DIR1, LOW);
    digitalWrite(PIN_M1_DIR2, HIGH);
  } else {
    digitalWrite(PIN_M1_DIR1, LOW);
    digitalWrite(PIN_M1_DIR2, LOW);
    pwm_L = 0;
  }
  ledcWrite(PIN_M1_PWM, pwm_L);

  int pwm_R = (int)(abs(right) * 255.0f);
  if (right > 0.01f) {
    digitalWrite(PIN_M2_DIR1, HIGH);
    digitalWrite(PIN_M2_DIR2, LOW);
  } else if (right < -0.01f) {
    digitalWrite(PIN_M2_DIR1, LOW);
    digitalWrite(PIN_M2_DIR2, HIGH);
  } else {
    digitalWrite(PIN_M2_DIR1, LOW);
    digitalWrite(PIN_M2_DIR2, LOW);
    pwm_R = 0;
  }
  ledcWrite(PIN_M2_PWM, pwm_R);
}

void setup_waveshare() {
  // Force motor direction and PWM pins low immediately
  pinMode(PIN_M1_DIR1, OUTPUT);
  pinMode(PIN_M1_DIR2, OUTPUT);
  pinMode(PIN_M1_PWM, OUTPUT);
  pinMode(PIN_M2_DIR1, OUTPUT);
  pinMode(PIN_M2_DIR2, OUTPUT);
  pinMode(PIN_M2_PWM, OUTPUT);
  
  digitalWrite(PIN_M1_DIR1, LOW);
  digitalWrite(PIN_M1_DIR2, LOW);
  digitalWrite(PIN_M1_PWM, LOW);
  digitalWrite(PIN_M2_DIR1, LOW);
  digitalWrite(PIN_M2_DIR2, LOW);
  digitalWrite(PIN_M2_PWM, LOW);

  // Setup LEDC (ESP32 hardware PWM - Arduino 3.0 API)
  ledcAttach(PIN_M1_PWM, 5000, 8);
  ledcAttach(PIN_M2_PWM, 5000, 8);
  ledcWrite(PIN_M1_PWM, 0);
  ledcWrite(PIN_M2_PWM, 0);

  // M1 encoder: GPIO 34 & 35 are INPUT-ONLY — no pull-up support.
  pinMode(PIN_M1_ENCA, INPUT);
  pinMode(PIN_M1_ENCB, INPUT);
  // M2 encoder: GPIO 27 & 16 are bidirectional — INPUT_PULLUP works!
  pinMode(PIN_M2_ENCA, INPUT_PULLUP); // BC1 (GPIO 27)
  pinMode(PIN_M2_ENCB, INPUT_PULLUP); // BC2 (GPIO 16)

  // Use CHANGE for maximum sensitivity (both rising and falling edges).
  attachInterrupt(digitalPinToInterrupt(PIN_M1_ENCA), left_encoder_isr, CHANGE);
  attachInterrupt(digitalPinToInterrupt(PIN_M2_ENCB), right_encoder_isr, CHANGE);

  // We will use Serial for communications
  Serial.begin(115200);
}

void loop_waveshare() {
  // Read serial JSON commands
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim();
    
    if (input.indexOf("\"T\":1") >= 0) {
      int l_idx = input.indexOf("\"L\":");
      int r_idx = input.indexOf("\"R\":");
      if (l_idx >= 0 && r_idx >= 0) {
        float left = input.substring(l_idx + 4, input.indexOf(",", l_idx)).toFloat();
        float right = input.substring(r_idx + 4, input.indexOf("}", r_idx)).toFloat();
        set_motor_speeds(left, right);
      }
    }
  }

  // Telemetry at 20Hz (every 50ms)
  unsigned long now = millis();
  if (now - last_telemetry_time >= 50) {
    last_telemetry_time = now;
    float raw_v = analogRead(PIN_BATTERY);
    float voltage_mv = raw_v * (3300.0f / 4095.0f) * 11.0f;

    Serial.print("{\"T\":1001,\"odl\":");
    Serial.print(left_ticks);
    Serial.print(",\"odr\":");
    Serial.print(right_ticks);
    Serial.print(",\"v\":");
    Serial.print((int)voltage_mv);
    Serial.println("}");
  }
}
