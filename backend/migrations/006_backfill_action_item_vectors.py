"""
Backfill existing action items to Pinecone vector database (ns4).

Iterates all users' action_items subcollections, generates embeddings
for each description, and upserts to Pinecone. Same approach as
005_backfill_memory_vectors.py.

Usage:
    python 006_backfill_action_item_vectors.py [--dry-run] [--uid USER_ID]

Environment:
    GOOGLE_APPLICATION_CREDENTIALS: Path to Firebase service account key
    PINECONE_API_KEY: Pinecone API key
    PINECONE_INDEX_NAME: Pinecone index name
    OPENAI_API_KEY: OpenAI API key for embeddings
"""

import firebase_admin
from firebase_admin import credentials, firestore
import sys
import os
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database.vector_db import upsert_action_item_vector
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(cred)
except ValueError:
    pass
except Exception as e:
    logger.error("Error initializing Firebase Admin SDK. Make sure GOOGLE_APPLICATION_CREDENTIALS is set.")
    logger.error(e)
    sys.exit(1)

db = firestore.client()


def get_all_user_ids():
    users_ref = db.collection('users')
    return [doc.id for doc in users_ref.stream()]


def get_user_action_items(uid: str):
    items_ref = db.collection('users').document(uid).collection('action_items')
    items = []
    for doc in items_ref.stream():
        data = doc.to_dict()
        data['id'] = doc.id
        items.append(data)
    return items


def process_action_item(uid: str, item: dict, dry_run: bool = False) -> dict:
    item_id = item['id']
    description = item.get('description', '')

    if not description:
        return {'status': 'skipped', 'reason': 'empty description', 'item_id': item_id}

    if dry_run:
        return {'status': 'dry_run', 'item_id': item_id}

    try:
        upsert_action_item_vector(uid, item_id, description)
        return {'status': 'success', 'item_id': item_id}
    except Exception as e:
        return {'status': 'error', 'item_id': item_id, 'error': str(e)}


def process_user(uid: str, dry_run: bool = False) -> dict:
    logger.info(f"Processing user: {uid}")
    items = get_user_action_items(uid)
    logger.info(f"  Found {len(items)} action items")

    results = {'total': len(items), 'success': 0, 'skipped': 0, 'errors': 0}

    for item in items:
        result = process_action_item(uid, item, dry_run)
        if result['status'] == 'success' or result['status'] == 'dry_run':
            results['success'] += 1
        elif result['status'] == 'skipped':
            results['skipped'] += 1
        else:
            results['errors'] += 1
            logger.error(f"  Error processing {result['item_id']}: {result.get('error')}")

    logger.info(f"  Done: {results['success']} ok, {results['skipped']} skipped, {results['errors']} errors")
    return results


def main():
    parser = argparse.ArgumentParser(description='Backfill action item vectors to Pinecone')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    parser.add_argument('--uid', type=str, help='Process a single user')
    args = parser.parse_args()

    if args.uid:
        user_ids = [args.uid]
    else:
        user_ids = get_all_user_ids()

    logger.info(f"Processing {len(user_ids)} users (dry_run={args.dry_run})")
    start = time.time()

    totals = {'users': 0, 'items': 0, 'success': 0, 'skipped': 0, 'errors': 0}

    for uid in user_ids:
        try:
            result = process_user(uid, dry_run=args.dry_run)
            totals['users'] += 1
            totals['items'] += result['total']
            totals['success'] += result['success']
            totals['skipped'] += result['skipped']
            totals['errors'] += result['errors']
        except Exception as e:
            logger.error(f"Failed to process user {uid}: {e}")
            totals['errors'] += 1

    elapsed = time.time() - start
    logger.info(f"\nDone in {elapsed:.1f}s")
    logger.info(f"Users: {totals['users']}, Items: {totals['items']}")
    logger.info(f"Success: {totals['success']}, Skipped: {totals['skipped']}, Errors: {totals['errors']}")


if __name__ == '__main__':
    main()
