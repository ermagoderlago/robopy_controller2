#!/bin/bash
mkdir -p ~/.cache/librespot
sed -i 's|ExecStart=.*|ExecStart=/usr/bin/librespot -n "raspotify (marcus)" -b 160 --backend pulseaudio --cache /home/robopy/.cache/librespot|g' ~/.config/systemd/user/librespot.service
systemctl --user daemon-reload
systemctl --user restart librespot
