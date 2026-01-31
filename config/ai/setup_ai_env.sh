#!/bin/bash
# ==============================================================================
# Robot AI Environment Setup
# ==============================================================================
# Source this file to set environment variables for the AI system.
# Copy to .env_ai_local and customize with your actual values.
#
# Usage: source setup_ai_env.sh
# ==============================================================================

# Gemini API Key (required for LLM and embeddings)
export GEMINI_API_KEY="AIzaSyDLp6AFFG2hMMaO8F-ngO2Y9cq6qJV7EMg"

# Google Cloud TTS/ASR Keys (optional, for cloud speech services)
# export GOOGLE_TTS_KEY=""
# export GOOGLE_ASR_KEY=""

# Home Assistant Token (from your HA instance)
# Generate at: http://your-ha:8123/profile -> Long-Lived Access Tokens
export HA_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIwZTU4MTA4OWEyZjk0YTk3OGQ2NWQyOTY5YWM3MjEyOSIsImlhdCI6MTc2MDI5ODE2MCwiZXhwIjoyMDc1NjU4MTYwfQ.8dymD3-cId-1v7kVIuvqXiZCrOF9ONVMvIh2_cGXgvo"

# Weather API Key (optional, for weather skill)
# export WEATHER_API_KEY=""

# AI Configuration
export ROBOT_AI_CONFIG_PATH="/home/robopy/robopy/robopi_controller/robopy_controller_host/config/ai/ai_config.yaml"
export ROBOT_AI_PROFILE="production"

# ChromaDB Path
export CHROMADB_PATH="/home/robopy/ChromaDB"

# Logging
export ROBOT_AI_LOG_LEVEL="INFO"

echo "✅ Robot AI environment variables set"
