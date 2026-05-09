sudo systemctl disable --now raspotify

mkdir -p ~/.config/systemd/user

cat << 'EOF' > ~/.config/systemd/user/librespot.service
[Unit]
Description=Librespot (Spotify Connect)

[Service]
ExecStart=/usr/bin/librespot -n "raspotify (marcus)" -b 160 --backend pulseaudio
Restart=always

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now librespot
sudo loginctl enable-linger robopy
