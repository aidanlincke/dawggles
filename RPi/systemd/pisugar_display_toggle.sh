#!/bin/bash
# PiSugar custom-button action. Sends SIGUSR1 to the dawggles main process,
# which toggles the OLED panel on/off (frame buffer and app state preserved).
# While the display is asleep, hardware button clicks are ignored, so a wake
# tap drops you back exactly where you left off.
#
# Install:
#   sudo cp pisugar_display_toggle.sh /usr/local/bin/
#   sudo chmod +x /usr/local/bin/pisugar_display_toggle.sh
# Then in the PiSugar web dashboard (http://<pi>:8421) set the custom
# single-tap (or whichever you prefer) button action to:
#   /usr/local/bin/pisugar_display_toggle.sh

exec /bin/systemctl kill --signal=SIGUSR1 dawggles.service
