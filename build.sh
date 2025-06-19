#!/usr/bin/env bash
set -e

echo "Starting build process..."

# Update package lists and install system dependencies
echo "Installing system dependencies..."
apt-get update && apt-get install -y ffmpeg

# Upgrade pip to latest version
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Install Python dependencies with error handling
echo "Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

echo "Build completed successfully!"
