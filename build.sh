#!/usr/bin/env bash
# Instalar ffmpeg
apt-get update && apt-get install -y ffmpeg
# Continuar con la instalación normal
pip install -r requirements.txt
