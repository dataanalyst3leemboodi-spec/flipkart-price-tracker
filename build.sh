#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Install Chrome for Selenium on Render
STORAGE_DIR=/opt/render/project/src/
if [[ ! -d $STORAGE_DIR/chrome ]]; then
  echo "...Installing Chrome..."
  mkdir -p $STORAGE_DIR/chrome
  cd $STORAGE_DIR/chrome
  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  dpkg -x google-chrome-stable_current_amd64.deb .
  rm google-chrome-stable_current_amd64.deb
  cd $STORAGE_DIR
else
  echo "...Using cached Chrome..."
fi
