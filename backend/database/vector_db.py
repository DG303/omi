import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    Range,
    VectorParams,
)

from utils.llm.clients import embeddings
import logging

logger = logging.getLogger(__name__)

# Qdrant replaces Pinecone. One collection per former Pinecone namespace; the
# original string vector ids ('{uid}-{conversation_id}', etc.) are mapped to
# deterministic UUIDv5 point ids because Qdrant only accepts UUID/int ids.
EMBEDDING_DIMENSIONS = 3072  # OpenAI text-embedding-3-large (and Gemini embedding-001 for screen activity)

CONVERSATIONS_COLLECTION = "omi_conversations"  # was ns1
MEMORIES_COLLECTION = "omi_memories"  # was ns2
X_POSTS_COLLECTION = "omi_x_posts"  # was ns_x
SCREEN_ACTIVITY_COLLECTION = "omi_screen_activity"  # was ns3
ACTION_ITEMS_COLLECTION = "omi_action_items"  # was ns4
TRANSCRIPT_CHUNKS_COLLECTION = "omi_transcript_chunks"  # was ns_tchunks

# Back-compat aliases: callers/tests reference these names.
MEMORIES_NAMESPACE = MEMORIES_COLLECTION
X_POSTS_NAMESPACE = X_POSTS_COLLECTION
SCREEN_ACTIVITY_NAMESPACE = SCREEN_ACTIVITY_COLLECTION
ACTION_ITEMS_NAMESPACE = ACTION_ITEMS_COLLECTION
TRANSCRIPT_CHUNKS_NAMESPACE = TRANSCRIPT_CHUNKS_COLLECTION

_ALL_COLLECTIONS = [
    CONVERSATIONS_COLLECTION,
    MEMORIES_COLLECTION,
    X_POSTS_COLLECTION,
    SCREEN_ACTIVITY_COLLECTION,
    ACTION_ITEMS_COLLECTION,
    TRANSCRIPT_CHUNKS_COLLECTION,
]


def _point_id(string_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, string_id))


def _ensure_collections(client: QdrantClient):
    existing = {c.name for c in client.get_collections().collections}
    for name in _ALL_COLLECTIONS:
        if name in existing:
            continue
        # Multiple services (backend, pusher) boot concurrently; losing the
        # create race is fine — the collection exists either way.
        try:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
            )
        except Exception as e:
            if 'already exists' in str(e):
                logger.info(f'qdrant collection {name} already exists (concurrent create)')
                continue
            raise
        client.create_payload_index(name, field_name='uid', field_schema=PayloadSchemaType.KEYWORD)
        client.create_payload_index(name, field_name='created_at', field_schema=PayloadSchemaType.INTEGER)
        if name == SCREEN_ACTIVITY_COLLECTION:
            client.create_payload_index(name, field_name='timestamp', field_schema=PayloadSchemaType.INTEGER)
            client.create_payload_index(name, field_name='appName', field_schema=PayloadSchemaType.KEYWORD)
        if name == TRANSCRIPT_CHUNKS_COLLECTION:
            client.create_payload_index(name, field_name='conversation_id', field_schema=PayloadSchemaType.KEYWORD)
        logger.info(f'created qdrant collection {name}')


if os.getenv('QDRANT_HOST'):
    index = QdrantClient(
        host=os.getenv('QDRANT_HOST'),
        port=int(os.getenv('QDRANT_PORT', '6333')),
        api_key=os.getenv('QDRANT_API_KEY') or None,
        https=os.getenv('QDRANT_HTTPS', 'false').lower() == 'true',
    )
    _ensure_collections(index)
else:
    index = None
    logger.warning('QDRANT_HOST not set, vector db disabled')


def _uid_filter(uid: str, extra: Optional[List[FieldCondition]] = None) -> Filter:
    must = [FieldCondition(key='uid', match=MatchValue(value=uid))]
    if extra:
        must.extend(extra)
    return Filter(must=must)


def _search(collection: str, vector: List[float], flt: Filter, limit: int):
    return index.query_points(collection_name=collection, query=vector, query_filter=flt, limit=limit).points


def _get_data(uid: str, conversation_id: str, vector: List[float]):
    return PointStruct(
        id=_point_id(f'{uid}-{conversation_id}'),
        vector=vector,
        payload={
            'uid': uid,
            'memory_id': conversation_id,
            'created_at': int(datetime.now(timezone.utc).timestamp()),
        },
    )


def upsert_vector(uid: str, conversation_id: str, vector: List[float]):
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vector upsert')
        return
    res = index.upsert(collection_name=CONVERSATIONS_COLLECTION, points=[_get_data(uid, conversation_id, vector)])
    logger.info(f'upsert_vector {res.status}')


def upsert_vector2(uid: str, conversation_id: str, vector: List[float], metadata: dict):
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vector upsert')
        return
    point = _get_data(uid, conversation_id, vector)
    point.payload.update(metadata)
    res = index.upsert(collection_name=CONVERSATIONS_COLLECTION, points=[point])
    logger.info(f'upsert_vector {res.status}')


def update_vector_metadata(uid: str, conversation_id: str, metadata: dict):
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vector metadata update')
        return
    metadata['uid'] = uid
    metadata['memory_id'] = conversation_id
    return index.set_payload(
        collection_name=CONVERSATIONS_COLLECTION,
        payload=metadata,
        points=[_point_id(f'{uid}-{conversation_id}')],
    )


def upsert_vectors(uid: str, vectors: List[List[float]], conversation_ids: List[str]):
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vectors upsert')
        return
    points = [_get_data(uid, cid, vector) for cid, vector in zip(conversation_ids, vectors)]
    res = index.upsert(collection_name=CONVERSATIONS_COLLECTION, points=points)
    logger.info(f'upsert_vectors {res.status}')


def query_vectors(query: str, uid: str, starts_at: int = None, ends_at: int = None, k: int = 5) -> List[str]:
    if index is None:
        logger.warning('Qdrant not initialized, skipping query_vectors')
        return []
    extra = []
    if starts_at is not None:
        extra.append(FieldCondition(key='created_at', range=Range(gte=starts_at, lte=ends_at)))
    xq = embeddings.embed_query(query)
    points = _search(CONVERSATIONS_COLLECTION, xq, _uid_filter(uid, extra), k)
    return [p.payload['memory_id'] for p in points]


def query_vectors_by_metadata(
    uid: str,
    vector: List[float],
    dates_filter: List[datetime],
    people: List[str],
    topics: List[str],
    entities: List[str],
    dates: List[str],
    limit: int = 5,
):
    if index is None:
        logger.warning('Qdrant not initialized, skipping query_vectors_by_metadata')
        return []

    must = [FieldCondition(key='uid', match=MatchValue(value=uid))]
    structured_should = []
    if people or topics or entities or dates:
        if people:
            structured_should.append(FieldCondition(key='people', match=MatchAny(any=people)))
        if topics:
            structured_should.append(FieldCondition(key='topics', match=MatchAny(any=topics)))
        if entities:
            structured_should.append(FieldCondition(key='entities', match=MatchAny(any=entities)))
    if dates_filter and len(dates_filter) == 2 and dates_filter[0] and dates_filter[1]:
        logger.info(f'dates_filter {dates_filter}')
        must.append(
            FieldCondition(
                key='created_at',
                range=Range(gte=int(dates_filter[0].timestamp()), lte=int(dates_filter[1].timestamp())),
            )
        )

    flt = Filter(must=must, should=structured_should or None)
    # Qdrant 'should' is a soft preference, not Pinecone's hard '$or'; replicate
    # the hard filter by requiring at least one structured match when present.
    if structured_should:
        flt = Filter(must=must + [Filter(should=structured_should)])
    points = _search(CONVERSATIONS_COLLECTION, vector, flt, 1000)

    if not points:
        if structured_should:
            retry_filter = Filter(must=must)
            logger.warning(
                f'query_vectors_by_metadata retrying without structured filters: '
                f'{json.dumps([m.model_dump() for m in must], default=str)}'
            )
            points = _search(CONVERSATIONS_COLLECTION, vector, retry_filter, 20)
        else:
            return []

    conversation_id_to_matches = defaultdict(int)
    for p in points:
        payload = p.payload
        conversation_id = payload['memory_id']
        for topic in topics:
            if topic in payload.get('topics', []):
                conversation_id_to_matches[conversation_id] += 1
        for entity in entities:
            if entity in payload.get('entities', []):
                conversation_id_to_matches[conversation_id] += 1
        for person in people:
            if person in payload.get('people_mentioned', []):
                conversation_id_to_matches[conversation_id] += 1

    conversations_id = [p.payload['memory_id'] for p in points]
    conversations_id.sort(key=lambda x: conversation_id_to_matches[x], reverse=True)
    return conversations_id[:limit] if len(conversations_id) > limit else conversations_id


def delete_vector(uid: str, conversation_id: str):
    """
    Delete a conversation vector from Qdrant.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vector delete')
        return
    vector_id = f'{uid}-{conversation_id}'
    result = index.delete(
        collection_name=CONVERSATIONS_COLLECTION, points_selector=PointIdsList(points=[_point_id(vector_id)])
    )
    logger.info(f'delete_vector {vector_id} {result.status}')


# ==========================================
# Memory Vector Functions
# For memory embeddings and semantic search
# ==========================================


def upsert_memory_vector(uid: str, memory_id: str, content: str, category: str):
    """
    Upsert a memory embedding to Qdrant.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping memory vector upsert')
        return None

    vector = embeddings.embed_query(content)
    point = PointStruct(
        id=_point_id(f'{uid}-{memory_id}'),
        vector=vector,
        payload={
            'uid': uid,
            'memory_id': memory_id,
            'category': category,
            'created_at': int(datetime.now(timezone.utc).timestamp()),
        },
    )
    res = index.upsert(collection_name=MEMORIES_COLLECTION, points=[point])
    logger.info(f'upsert_memory_vector {memory_id} {res.status}')
    return vector


def upsert_memory_vectors_batch(uid: str, items: List[dict]) -> int:
    """
    Upsert many memory embeddings to Qdrant in a single request.

    Each item must be a dict with keys: 'memory_id', 'content', 'category'.
    Batching cuts latency from N embedding calls + N upserts to one embedding
    call + one upsert. Used by POST /v3/memories/batch and the dev batch API.
    Returns the number of vectors written (0 if Qdrant is not configured).
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping memory vector batch upsert')
        return 0

    if not items:
        return 0

    contents = [item['content'] for item in items]
    vectors = embeddings.embed_documents(contents)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    points = [
        PointStruct(
            id=_point_id(f"{uid}-{item['memory_id']}"),
            vector=vectors[i],
            payload={
                'uid': uid,
                'memory_id': item['memory_id'],
                'category': item['category'],
                'created_at': now_ts,
            },
        )
        for i, item in enumerate(items)
    ]
    res = index.upsert(collection_name=MEMORIES_COLLECTION, points=points)
    logger.info(f'upsert_memory_vectors_batch count={len(points)} {res.status}')
    return len(points)


def find_similar_memories(uid: str, content: str, threshold: float = 0.85, limit: int = 5) -> List[dict]:
    """
    Find memories similar to the given content.
    Returns list of matches with similarity scores.
    Used for duplicate detection and semantic search.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping similarity search')
        return []

    vector = embeddings.embed_query(content)
    points = _search(MEMORIES_COLLECTION, vector, _uid_filter(uid), limit)

    results = []
    for match in points:
        if match.score >= threshold:
            results.append(
                {
                    'memory_id': match.payload.get('memory_id'),
                    'category': match.payload.get('category'),
                    'score': match.score,
                }
            )

    return results


def check_memory_duplicate(uid: str, content: str, threshold: float = 0.85) -> dict | None:
    """
    Check if a similar memory already exists.
    Returns the duplicate info if found, None otherwise.
    """
    similar = find_similar_memories(uid, content, threshold=threshold, limit=1)
    if similar:
        logger.warning(f'Found duplicate memory: {similar[0]}')
        return similar[0]
    return None


def search_memories_by_vector(uid: str, query: str, limit: int = 10) -> List[str]:
    """
    Semantic search for memories.
    Returns list of memory_ids ordered by relevance.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping memory search')
        return []

    vector = embeddings.embed_query(query)
    points = _search(MEMORIES_COLLECTION, vector, _uid_filter(uid), limit)
    return [match.payload.get('memory_id') for match in points]


def delete_memory_vector(uid: str, memory_id: str):
    """
    Delete a memory vector from Qdrant.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping memory vector delete')
        return

    vector_id = f'{uid}-{memory_id}'
    result = index.delete(
        collection_name=MEMORIES_COLLECTION, points_selector=PointIdsList(points=[_point_id(vector_id)])
    )
    logger.info(f'delete_memory_vector {vector_id} {result.status}')


# ==========================================
# X (Twitter) Post Vector Functions
# Semantic search over the user's raw imported tweets/bookmarks.
# ==========================================


def upsert_x_post_vectors_batch(uid: str, items: List[dict]) -> int:
    """Upsert X post embeddings in one request. Each item: {'post_id', 'content', 'kind'}.
    Returns the number of vectors written (0 if Qdrant is not configured)."""
    if index is None:
        logger.warning('Qdrant not initialized, skipping x_post vector batch upsert')
        return 0
    items = [it for it in items if (it.get('content') or '').strip()]
    if not items:
        return 0

    vectors = embeddings.embed_documents([it['content'] for it in items])
    now_ts = int(datetime.now(timezone.utc).timestamp())
    points = [
        PointStruct(
            id=_point_id(f"{uid}-x-{it['post_id']}"),
            vector=vectors[i],
            payload={
                'uid': uid,
                'post_id': str(it['post_id']),
                'kind': it.get('kind', 'tweet'),
                'created_at': now_ts,
            },
        )
        for i, it in enumerate(items)
    ]
    res = index.upsert(collection_name=X_POSTS_COLLECTION, points=points)
    logger.info(f'upsert_x_post_vectors_batch count={len(points)} {res.status}')
    return len(points)


def find_similar_x_posts(uid: str, content: str, limit: int = 10) -> List[dict]:
    """Semantic search over the user's X posts. Returns [{post_id, kind, score}]."""
    if index is None:
        logger.warning('Qdrant not initialized, skipping x_post similarity search')
        return []
    vector = embeddings.embed_query(content)
    points = _search(X_POSTS_COLLECTION, vector, _uid_filter(uid), limit)
    return [
        {
            'post_id': m.payload.get('post_id'),
            'kind': m.payload.get('kind'),
            'score': m.score,
        }
        for m in points
    ]


# ==========================================
# Screen Activity Vector Functions
# For screenshot embeddings (Gemini embedding-001, 3072-dim)
# ==========================================


def upsert_screen_activity_vectors(uid: str, rows: List[dict]) -> int:
    """Batch upsert screenshot embeddings."""
    if index is None:
        logger.warning('Qdrant not initialized, skipping screen activity vector upsert')
        return 0

    points = []
    for row in rows:
        embedding = row.get('embedding')
        if not embedding:
            continue
        points.append(
            PointStruct(
                id=_point_id(f'{uid}-sa-{row["id"]}'),
                vector=embedding,
                payload={
                    'uid': uid,
                    'screenshot_id': str(row['id']),
                    'timestamp': (
                        int(datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp())
                        if isinstance(row['timestamp'], str)
                        else int(row['timestamp'])
                    ),
                    'appName': row.get('appName', ''),
                },
            )
        )

    if not points:
        return 0

    upserted = 0
    for i in range(0, len(points), 100):
        chunk = points[i : i + 100]
        index.upsert(collection_name=SCREEN_ACTIVITY_COLLECTION, points=chunk)
        upserted += len(chunk)

    logger.info(f'upsert_screen_activity_vectors uid={uid} count={upserted}')
    return upserted


def search_screen_activity_vectors(
    uid: str,
    query_vector: List[float],
    start_date: int = None,
    end_date: int = None,
    app_filter: str = None,
    k: int = 10,
) -> List[dict]:
    """Vector search across screenshot embeddings."""
    if index is None:
        logger.warning('Qdrant not initialized, skipping screen activity search')
        return []

    extra = []
    if start_date and end_date:
        extra.append(FieldCondition(key='timestamp', range=Range(gte=start_date, lte=end_date)))
    elif start_date:
        extra.append(FieldCondition(key='timestamp', range=Range(gte=start_date)))
    elif end_date:
        extra.append(FieldCondition(key='timestamp', range=Range(lte=end_date)))
    if app_filter:
        extra.append(FieldCondition(key='appName', match=MatchValue(value=app_filter)))

    points = _search(SCREEN_ACTIVITY_COLLECTION, query_vector, _uid_filter(uid, extra), k)
    return [
        {
            'screenshot_id': match.payload.get('screenshot_id'),
            'timestamp': match.payload.get('timestamp'),
            'appName': match.payload.get('appName'),
            'score': match.score,
        }
        for match in points
    ]


def delete_screen_activity_vectors(uid: str, ids: List[int]):
    """Delete screen activity vectors by screenshot IDs."""
    if index is None:
        return
    point_ids = [_point_id(f'{uid}-sa-{sid}') for sid in ids]
    index.delete(collection_name=SCREEN_ACTIVITY_COLLECTION, points_selector=PointIdsList(points=point_ids))


# ==========================================
# Action Item Vector Functions
# ==========================================


def upsert_action_item_vector(uid: str, action_item_id: str, description: str):
    if index is None:
        logger.warning('Qdrant not initialized, skipping action item vector upsert')
        return None

    vector = embeddings.embed_query(description)
    point = PointStruct(
        id=_point_id(f'{uid}-ai-{action_item_id}'),
        vector=vector,
        payload={
            'uid': uid,
            'action_item_id': action_item_id,
            'created_at': int(datetime.now(timezone.utc).timestamp()),
        },
    )
    res = index.upsert(collection_name=ACTION_ITEMS_COLLECTION, points=[point])
    logger.info(f'upsert_action_item_vector {action_item_id} {res.status}')
    return vector


def upsert_action_item_vectors_batch(uid: str, items: List[dict]) -> int:
    if index is None:
        logger.warning('Qdrant not initialized, skipping action item vector batch upsert')
        return 0

    if not items:
        return 0

    descriptions = [item['description'] for item in items]
    vectors = embeddings.embed_documents(descriptions)

    now_ts = int(datetime.now(timezone.utc).timestamp())
    points = [
        PointStruct(
            id=_point_id(f"{uid}-ai-{item['action_item_id']}"),
            vector=vectors[i],
            payload={
                'uid': uid,
                'action_item_id': item['action_item_id'],
                'created_at': now_ts,
            },
        )
        for i, item in enumerate(items)
    ]
    res = index.upsert(collection_name=ACTION_ITEMS_COLLECTION, points=points)
    logger.info(f'upsert_action_item_vectors_batch count={len(points)} {res.status}')
    return len(points)


def search_action_items_by_vector(uid: str, query: str, limit: int = 10, min_score: float = 0.3) -> List[str]:
    if index is None:
        logger.warning('Qdrant not initialized, skipping action item search')
        return []

    vector = embeddings.embed_query(query)
    points = _search(ACTION_ITEMS_COLLECTION, vector, _uid_filter(uid), limit)

    top_score = points[0].score if points else None
    kept = [m for m in points if m.score >= min_score]
    logger.info(
        f'search_action_items_by_vector uid={uid} matches={len(points)} kept={len(kept)} '
        f'top_score={top_score} min_score={min_score}'
    )
    return [m.payload.get('action_item_id') for m in kept]


def find_similar_action_items(uid: str, query: str, threshold: float = 0.6, limit: int = 10) -> List[dict]:
    """
    Find action items semantically similar to the given query text. Used to
    feed the conversation extraction prompt with potentially-duplicate open
    tasks so the LLM can suppress true duplicates.

    Returns matches at or above the threshold. Each result is
    `{'action_item_id': str, 'score': float}` ordered by relevance.
    Qdrant or embedding failures degrade silently to an empty list — the
    caller treats "no candidates" as "user has nothing relevant," which is
    the same behavior as a brand-new user.
    """
    if index is None:
        return []

    try:
        vector = embeddings.embed_query(query)
        points = _search(ACTION_ITEMS_COLLECTION, vector, _uid_filter(uid), limit)
        kept = []
        dropped_no_id = 0
        for m in points:
            if m.score < threshold:
                continue
            aid = (m.payload or {}).get('action_item_id')
            if not aid:
                dropped_no_id += 1
                continue
            kept.append({'action_item_id': aid, 'score': m.score})
        top_score = points[0].score if points else None
        logger.info(
            f'find_similar_action_items uid={uid} matches={len(points)} '
            f'kept={len(kept)} dropped_no_id={dropped_no_id} '
            f'top_score={top_score} threshold={threshold}'
        )
        return kept
    except Exception as e:
        logger.exception(f'find_similar_action_items failed uid={uid}: {e}')
        return []


def delete_action_item_vector(uid: str, action_item_id: str):
    if index is None:
        logger.warning('Qdrant not initialized, skipping action item vector delete')
        return

    vector_id = f'{uid}-ai-{action_item_id}'
    result = index.delete(
        collection_name=ACTION_ITEMS_COLLECTION, points_selector=PointIdsList(points=[_point_id(vector_id)])
    )
    logger.info(f'delete_action_item_vector {vector_id} {result.status}')


def delete_action_item_vectors_batch(uid: str, action_item_ids: List[str]):
    if index is None:
        return
    if not action_item_ids:
        return
    point_ids = [_point_id(f'{uid}-ai-{aid}') for aid in action_item_ids]
    index.delete(collection_name=ACTION_ITEMS_COLLECTION, points_selector=PointIdsList(points=point_ids))
    logger.info(f'delete_action_item_vectors_batch count={len(point_ids)}')


def delete_conversation_vectors_batch(uid: str, conversation_ids: List[str]):
    """Delete a user's conversation vectors in one batched, chunked call.

    Chunked so a single failure can't abandon the rest. Used by account
    deletion to purge all of a user's conversation vectors.
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping conversation vector batch delete')
        return
    if not conversation_ids:
        return
    point_ids = [_point_id(f'{uid}-{cid}') for cid in conversation_ids]
    for i in range(0, len(point_ids), 1000):
        index.delete(
            collection_name=CONVERSATIONS_COLLECTION, points_selector=PointIdsList(points=point_ids[i : i + 1000])
        )
    logger.info(f'delete_conversation_vectors_batch count={len(point_ids)}')


def delete_memory_vectors_batch(uid: str, memory_ids: List[str]) -> int:
    """Delete a user's memory vectors in batched, chunked calls.

    Each chunk is individually wrapped in try/except so a transient failure
    on one chunk does not abandon the rest. Returns the number of vectors
    successfully deleted (0 if Qdrant is not configured).
    """
    if index is None:
        logger.warning('Qdrant not initialized, skipping memory vector batch delete')
        return 0
    if not memory_ids:
        return 0
    point_ids = [_point_id(f'{uid}-{mid}') for mid in memory_ids]
    total_deleted = 0
    for i in range(0, len(point_ids), 1000):
        chunk = point_ids[i : i + 1000]
        try:
            index.delete(collection_name=MEMORIES_COLLECTION, points_selector=PointIdsList(points=chunk))
            total_deleted += len(chunk)
        except Exception:
            logger.warning(f'delete_memory_vectors_batch chunk failed uid={uid} chunk={i // 1000}')
    logger.info(f'delete_memory_vectors_batch uid={uid} total_deleted={total_deleted}')
    return total_deleted


# ---------------------------------------------------------------------------
# Transcript chunks: verbatim retrieval over raw conversation transcripts.
# Conversation vectors embed only the structured SUMMARY, so specific details
# (exact dates, names, numbers, one-off mentions) are not findable
# semantically. Chunk vectors make the raw transcript searchable.
#
# Privacy: chunk TEXT is embedded but never stored in the Qdrant payload —
# transcripts are encrypted at rest in Firestore, and mirroring them as
# plaintext payload would bypass that. Readers re-hydrate the text from
# Firestore via (conversation_id, chunk_index).


def upsert_transcript_chunk_vectors(uid: str, conversation_id: str, chunks: List[dict]) -> int:
    """chunks: [{'text': str, 'created_at': int unix ts, 'chunk_index': int}]"""
    if index is None:
        logger.warning('Qdrant not initialized, skipping transcript chunk upsert')
        return 0
    chunks = [c for c in chunks if (c.get('text') or '').strip()]
    if not chunks:
        return 0

    vectors = embeddings.embed_documents([c['text'] for c in chunks])
    points = []
    for c, v in zip(chunks, vectors):
        points.append(
            PointStruct(
                id=_point_id(f"{uid}-{conversation_id}-c{c['chunk_index']}"),
                vector=v,
                payload={
                    'uid': uid,
                    'conversation_id': conversation_id,
                    'chunk_index': c['chunk_index'],
                    'created_at': int(c['created_at']),
                },
            )
        )

    upserted = 0
    for i in range(0, len(points), 100):
        index.upsert(collection_name=TRANSCRIPT_CHUNKS_COLLECTION, points=points[i : i + 100])
        upserted += len(points[i : i + 100])
    logger.info(f'upsert_transcript_chunk_vectors uid={uid} conversation={conversation_id} count={upserted}')
    return upserted


def search_transcript_chunks(
    uid: str, query: str, limit: int = 20, starts_at: int = None, ends_at: int = None
) -> List[dict]:
    """Semantic search over transcript chunks. Returns chunk references
    [{conversation_id, chunk_index, created_at, score}] — hydrate text from
    Firestore (utils.conversations.transcript_chunks.hydrate_chunk_texts)."""
    if index is None:
        return []
    vector = embeddings.embed_query(query)
    extra = []
    if starts_at is not None and ends_at is not None:
        extra.append(FieldCondition(key='created_at', range=Range(gte=int(starts_at), lte=int(ends_at))))
    points = _search(TRANSCRIPT_CHUNKS_COLLECTION, vector, _uid_filter(uid, extra), limit)
    results = []
    for m in points:
        md = m.payload or {}
        results.append(
            {
                'created_at': int(md['created_at']) if md.get('created_at') is not None else None,
                'conversation_id': md.get('conversation_id'),
                'chunk_index': int(md['chunk_index']) if md.get('chunk_index') is not None else None,
                'score': m.score,
            }
        )
    return results


def _delete_chunks_by_conversation(uid: str, conversation_id: str) -> bool:
    flt = Filter(
        must=[
            FieldCondition(key='uid', match=MatchValue(value=uid)),
            FieldCondition(key='conversation_id', match=MatchValue(value=conversation_id)),
        ]
    )
    index.delete(collection_name=TRANSCRIPT_CHUNKS_COLLECTION, points_selector=FilterSelector(filter=flt))
    return True


def delete_transcript_chunk_vectors(uid: str, conversation_id: str):
    """Delete all chunk vectors for one conversation (payload filter delete)."""
    if index is None:
        return
    try:
        _delete_chunks_by_conversation(uid, conversation_id)
        logger.info(f'delete_transcript_chunk_vectors uid={uid} conversation={conversation_id}')
    except Exception:
        logger.warning(f'delete_transcript_chunk_vectors failed uid={uid} conversation={conversation_id}')


def delete_transcript_chunk_vectors_batch(uid: str, conversation_ids: List[str]) -> int:
    """Account-deletion purge: drop all transcript-chunk vectors for the user's conversations."""
    if index is None or not conversation_ids:
        return 0
    deleted = 0
    for conversation_id in conversation_ids:
        try:
            _delete_chunks_by_conversation(uid, conversation_id)
            deleted += 1
        except Exception:
            logger.warning(f'delete_transcript_chunk_vectors_batch failed uid={uid} conversation={conversation_id}')
    logger.info(f'delete_transcript_chunk_vectors_batch uid={uid} conversations_purged={deleted}')
    return deleted
