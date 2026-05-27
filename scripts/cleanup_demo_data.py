from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.database_config import SessionLocal
from database.models import User
from scripts.seed_demo_data import DEMO_PROJECT_ID, DEMO_SEED_VERSION, DEMO_USER_EMAIL, cleanup_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove controlled demo data created for defense presentation.")
    parser.add_argument("--delete-user", action="store_true", help="Also delete the demo user account.")
    parser.add_argument("--email", default=DEMO_USER_EMAIL)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        cleanup_demo_data(db)
        deleted_user = False
        if args.delete_user:
            user = db.query(User).filter(User.email == args.email).first()
            if user is not None:
                db.delete(user)
                deleted_user = True
        db.commit()
        print(
            json.dumps(
                {
                    "status": "ok",
                    "project_id": DEMO_PROJECT_ID,
                    "seed_version": DEMO_SEED_VERSION,
                    "deleted_user": deleted_user,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
