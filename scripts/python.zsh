#!/bin/zsh
# Resolve a machine-local environment, retaining compatibility with older Macs.
task_python="$HOME/Library/Application Support/ParkingWatch/venvs/$(/usr/bin/uname -m)/bin/python"
if [[ ! -x "$task_python" ]]; then
    task_python="$PWD/.venv/bin/python"
fi
