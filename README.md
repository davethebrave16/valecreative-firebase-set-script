# Valecreative Claim Set Script

Command-line utilities for managing Firebase custom claims and Firestore data.

## Requirements

- Python 3.10+
- Firebase project with Authentication and Firestore enabled
- Service account key with appropriate permissions

## Setup

1. Create and activate virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment variables (see Configuration section below)

## Configuration

Create a `.env` file in the project root with the following variables:

```env
# Required: Path to your Firebase service account JSON file
SERVICE_ACCOUNT_PATH=./google-sa-key_staging.json

# Required: Your Firebase project ID
PROJECT_ID=your-firebase-project-id

# For set_admin_claim.py
USERS_TO_SET_ADMIN=user1@example.com,user2@example.com
USERS_TO_REMOVE_ADMIN=user3@example.com
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SERVICE_ACCOUNT_PATH` | Yes | Path to Firebase service account JSON file |
| `PROJECT_ID` | Yes | Firebase project ID |
| `USERS_TO_SET_ADMIN` | No | Comma-separated emails/UIDs to grant admin claim |
| `USERS_TO_REMOVE_ADMIN` | No | Comma-separated emails/UIDs to remove admin claim |

Users can be specified by email address or Firebase UID.

## Usage

### Set/Remove Admin Claims

```bash
python set_admin_claim.py
```

The script will:
- Initialize Firebase Admin SDK using the service account
- Process users to grant the claim (if specified)
- Process users to remove the claim (if specified)
- Print a summary of successful/failed operations
