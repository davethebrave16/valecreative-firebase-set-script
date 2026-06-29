#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import firebase_admin
from firebase_admin import auth, credentials
from dotenv import load_dotenv


def load_configuration() -> tuple[str, str]:
    load_dotenv()

    service_account_path = os.getenv('SERVICE_ACCOUNT_PATH')
    project_id = os.getenv('PROJECT_ID')

    if not service_account_path:
        print("Error: SERVICE_ACCOUNT_PATH environment variable not set")
        print("Please set SERVICE_ACCOUNT_PATH in your .env file")
        sys.exit(1)

    if not project_id:
        print("Error: PROJECT_ID environment variable not set")
        print("Please set PROJECT_ID in your .env file")
        sys.exit(1)

    if not os.path.exists(service_account_path):
        print(f"Error: Service account file not found at {service_account_path}")
        sys.exit(1)

    return service_account_path, project_id


def initialize_firebase(service_account_path: str, project_id: str):
    try:
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = service_account_path
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred, {'projectId': project_id})
    except Exception as e:
        print(f"Error initializing Firebase Admin SDK: {e}")
        sys.exit(1)


def get_user_by_email(email: str) -> tuple[str, str] | None:
    try:
        user = auth.get_user_by_email(email)
        return user.uid, user.email
    except auth.UserNotFoundError:
        return None
    except Exception as e:
        print(f"Error looking up user by email {email}: {e}")
        return None


def get_user_by_uid(uid: str) -> tuple[str, str] | None:
    try:
        user = auth.get_user(uid)
        return user.uid, user.email
    except auth.UserNotFoundError:
        return None
    except Exception as e:
        print(f"Error looking up user by UID {uid}: {e}")
        return None


def set_admin_claim_by_email(email: str, is_admin: bool) -> bool:
    result = get_user_by_email(email)
    if not result:
        print(f"✗ User with email {email} not found")
        return False

    uid, user_email = result
    return set_admin_claim(uid, user_email, is_admin)


def set_admin_claim(uid: str, email: str, is_admin: bool) -> bool:
    try:
        user = auth.get_user(uid)
        prev = user.custom_claims or {}
        current_admin_status = prev.get('admin', False)

        if current_admin_status == bool(is_admin):
            action = "already has" if is_admin else "already doesn't have"
            print(f"⚠ {email} ({uid}) {action} admin claim")
            return True

        updated_claims = {
            **prev,
            'admin': bool(is_admin)
        }

        auth.set_custom_user_claims(uid, updated_claims)

        action = "granted" if is_admin else "removed"
        print(f"✓ {email} ({uid}) admin claim {action}")
        return True

    except auth.UserNotFoundError:
        print(f"✗ User {uid} not found")
        return False
    except Exception as e:
        print(f"✗ Error setting admin claim for {uid}: {e}")
        return False


def process_user_list(user_list: str, is_admin: bool, action: str) -> None:
    if not user_list.strip():
        print(f"No users to {action}")
        return

    users = [user.strip() for user in user_list.split(',') if user.strip()]
    print(f"\n{action.capitalize()} admin claims for {len(users)} users:")

    success_count = 0
    for user in users:
        if '@' in user:
            if set_admin_claim_by_email(user, is_admin):
                success_count += 1
        else:
            result = get_user_by_uid(user)
            if result:
                uid, email = result
                if set_admin_claim(uid, email, is_admin):
                    success_count += 1
            else:
                print(f"✗ User with UID {user} not found")

    print(f"\nSummary: {success_count}/{len(users)} users processed successfully")


def main():
    try:
        service_account_path, project_id = load_configuration()

        initialize_firebase(service_account_path, project_id)
        print("Firebase Admin SDK initialized successfully")

        users_to_set_admin = os.getenv('USERS_TO_SET_ADMIN', '')
        users_to_remove_admin = os.getenv('USERS_TO_REMOVE_ADMIN', '')

        if users_to_set_admin:
            process_user_list(users_to_set_admin, True, "setting")

        if users_to_remove_admin:
            process_user_list(users_to_remove_admin, False, "removing")

        if not users_to_set_admin and not users_to_remove_admin:
            print("No environment variables found:")
            print("  USERS_TO_SET_ADMIN - comma-separated list of emails/UIDs to grant admin")
            print("  USERS_TO_REMOVE_ADMIN - comma-separated list of emails/UIDs to remove admin")
            print("\nExample:")
            print("  export USERS_TO_SET_ADMIN='user@example.com,user2@example.com'")
            print("  export USERS_TO_REMOVE_ADMIN='user3@example.com,user4@example.com'")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())

