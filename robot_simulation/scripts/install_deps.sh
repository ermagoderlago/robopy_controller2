#!/bin/bash
set -e

echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4
echo 'nameserver 192.168.22.45' > /etc/resolv.conf
echo 'nameserver 192.168.22.46' >> /etc/resolv.conf

apt-get update
apt-get install -y ros-jazzy-ros-gz ros-jazzy-ros-gz-sim ros-jazzy-ros-gz-bridge ros-jazzy-ros-gz-interfaces ros-jazzy-foxglove-bridge
