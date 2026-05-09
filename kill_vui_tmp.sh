#!/bin/bash
kill $(pgrep -f respeaker_vui_node) 2>/dev/null && echo "VUI_KILLED" || echo "VUI_NOT_FOUND"
