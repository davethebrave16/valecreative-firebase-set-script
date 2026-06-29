#!/bin/bash
set -e

if [ ! -d ".venv" ]; then
	echo "Virtual environment not found. Run ./setup.sh first."
	exit 1
fi

if [ ! -f ".env" ]; then
	echo ".env file not found. Run ./setup.sh first."
	exit 1
fi

.venv/bin/python set_admin_claim.py
