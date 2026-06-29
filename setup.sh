#!/bin/bash
set -e

if [ ! -d ".venv" ]; then
	echo "Creating virtual environment..."
	python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f ".env" ]; then
	cat > .env << 'EOF'
SERVICE_ACCOUNT_PATH=./google-sa-key-staging.json
PROJECT_ID=your-firebase-project-id

USERS_TO_SET_ADMIN=
USERS_TO_REMOVE_ADMIN=
EOF
	echo ".env file created — fill in PROJECT_ID and user lists before running"
else
	echo ".env already exists, skipping"
fi

echo "Setup complete. Run ./run.sh to execute."
