"""
Share Token Management for NoteDiscovery
Handles creating, storing, and revoking share tokens for public note access.
"""

import json
import logging
import re
import secrets
import string
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import threading

from .utils import validate_path_security

logger = logging.getLogger("uvicorn.error")

# Thread lock for safe concurrent access
_lock = threading.Lock()

# A custom slug replaces the generated token, so it lands in a URL path segment and
# has to survive being typed and read aloud: same alphabet as generate_token, no
# separators. The minimum length keeps single letters out of the shared namespace.
SLUG_MIN_LENGTH = 3
SLUG_MAX_LENGTH = 64
_SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')


class ShareSlugError(ValueError):
    """
    A requested slug cannot be used.

    `reason` is a stable code ('too_short', 'too_long', 'invalid_chars', 'taken')
    rather than prose, because the message the user sees is translated client-side.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate_slug(slug: Any) -> str:
    """
    Check a user-supplied slug and return it in the form it should be stored.

    Raises ShareSlugError for anything the URL or the tokens file cannot hold.
    """
    if not isinstance(slug, str):
        raise ShareSlugError('invalid_chars')

    candidate = slug.strip()
    if len(candidate) < SLUG_MIN_LENGTH:
        raise ShareSlugError('too_short')
    if len(candidate) > SLUG_MAX_LENGTH:
        raise ShareSlugError('too_long')
    if not _SLUG_RE.match(candidate):
        raise ShareSlugError('invalid_chars')
    return candidate


def _find_conflicting_token(
    tokens: Dict[str, Dict[str, Any]], slug: str, note_path: Optional[str] = None
) -> Optional[str]:
    """
    Return an existing token that would collide with slug, or None.

    Matching ignores case: two links differing only in case read as the same name to
    whoever types one. A token already pointing at note_path is not a conflict, so
    re-submitting a note's current name is a no-op rather than an error.
    """
    target = slug.casefold()
    for token, info in tokens.items():
        if token.casefold() != target:
            continue
        if note_path is not None and info.get('path') == note_path:
            continue
        return token
    return None


def is_slug_available(data_dir: str, slug: str, note_path: Optional[str] = None) -> bool:
    """
    Report whether slug is free, optionally treating note_path's own token as free.

    Advisory only: callers still have to handle ShareSlugError from the write, since
    another request can claim the name in between.
    """
    return _find_conflicting_token(load_tokens(data_dir), slug, note_path) is None


def _token_references_accessible_file(storage_dir: str, rel_path: Any) -> bool:
    """
    Return True if rel_path is a string that resolves to a regular file under storage_dir
    and passes the same path boundary check used elsewhere in the app.
    """
    if not rel_path or not isinstance(rel_path, str):
        return False
    try:
        full = (Path(storage_dir) / rel_path).resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    if not validate_path_security(str(storage_dir), full):
        return False
    return full.is_file()


def _prune_inaccessible_unsafe(data_dir: str) -> int:
    """
    Remove share tokens whose stored path is missing or not under the notes directory.
    Call only while holding _lock. Returns the number of tokens removed.
    """
    tokens = load_tokens(data_dir)
    if not tokens:
        return 0
    kept: Dict[str, Dict[str, Any]] = {}
    for token, info in tokens.items():
        path = info.get("path")
        if _token_references_accessible_file(data_dir, path):
            kept[token] = info
    removed = len(tokens) - len(kept)
    if removed and not save_tokens(data_dir, kept):
        return 0
    return removed


def prune_inaccessible_share_tokens(data_dir: str) -> int:
    """
    Remove entries whose path no longer resolves to a file under the storage root.
    Called automatically after create/revoke share; may also be used from tests or one-off maintenance.
    """
    with _lock:
        return _prune_inaccessible_unsafe(data_dir)


def generate_token(length: int = 16) -> str:
    """Generate a URL-safe random token."""
    # Use alphanumeric + underscore/hyphen (URL-safe)
    alphabet = string.ascii_letters + string.digits + '_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def get_tokens_file_path(data_dir: str) -> Path:
    """Get the path to the share tokens file."""
    return Path(data_dir) / '.share-tokens.json'


def load_tokens(data_dir: str) -> Dict[str, Dict[str, Any]]:
    """Load share tokens from file."""
    tokens_file = get_tokens_file_path(data_dir)
    
    if not tokens_file.exists():
        return {}
    
    try:
        with open(tokens_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_tokens(data_dir: str, tokens: Dict[str, Dict[str, Any]]) -> bool:
    """Save share tokens to file."""
    tokens_file = get_tokens_file_path(data_dir)
    
    try:
        # Ensure parent directory exists
        tokens_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(tokens_file, 'w', encoding='utf-8') as f:
            json.dump(tokens, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        logger.error("Failed to save share tokens: %s", e)
        return False


def _rename_token_unsafe(
    data_dir: str,
    tokens: Dict[str, Dict[str, Any]],
    note_path: str,
    old_token: str,
    new_slug: str,
) -> Optional[str]:
    """
    Move a note's share entry to a new token, keeping theme and creation date.

    The old URL stops working: a note has one token, so the rename is a swap rather
    than an addition. Both halves happen in one dict and one write, so a reader can
    never see the note shared twice or not at all.
    Call only while holding _lock.
    """
    if _find_conflicting_token(tokens, new_slug, note_path) is not None:
        raise ShareSlugError('taken')

    tokens[new_slug] = tokens.pop(old_token)

    if not save_tokens(data_dir, tokens):
        _prune_inaccessible_unsafe(data_dir)
        return None
    _prune_inaccessible_unsafe(data_dir)
    return new_slug


def create_share_token(
    data_dir: str, note_path: str, theme: str = "light", slug: Optional[str] = None
) -> Optional[str]:
    """
    Create a share token for a note.
    If the note already has a token, returns the existing one.
    
    Args:
        data_dir: Path to the data directory
        note_path: Path to the note (relative to notes_dir)
        theme: The theme to use when viewing the shared note
        slug: Optional custom token. On an already-shared note a slug that differs
            from the current token renames the link, which retires the old URL;
            passing None leaves an existing link untouched.
    
    Returns:
        The share token, or None on error
    
    Raises:
        ShareSlugError: the slug is malformed or already in use.
    """
    desired = validate_slug(slug) if slug is not None else None

    with _lock:
        tokens = load_tokens(data_dir)
        
        # Check if note already has a token
        for token, info in tokens.items():
            if info.get('path') == note_path:
                if desired is None or desired == token:
                    _prune_inaccessible_unsafe(data_dir)
                    return token
                return _rename_token_unsafe(data_dir, tokens, note_path, token, desired)

        if desired is not None:
            if _find_conflicting_token(tokens, desired, note_path) is not None:
                raise ShareSlugError('taken')
            token = desired
        else:
            # Generate new token
            token = generate_token()
            
            # Ensure uniqueness (extremely unlikely collision, but check anyway)
            while token in tokens:
                token = generate_token()
        
        # Store token with theme
        tokens[token] = {
            'path': note_path,
            'theme': theme,
            'created': datetime.now(timezone.utc).isoformat()
        }
        
        if save_tokens(data_dir, tokens):
            _prune_inaccessible_unsafe(data_dir)
            return token
        _prune_inaccessible_unsafe(data_dir)
        return None


def get_share_token(data_dir: str, note_path: str) -> Optional[str]:
    """
    Get the share token for a note, if it exists.
    
    Args:
        data_dir: Path to the data directory
        note_path: Path to the note
    
    Returns:
        The share token, or None if not shared
    """
    tokens = load_tokens(data_dir)
    
    for token, info in tokens.items():
        if info.get('path') == note_path:
            return token
    
    return None


def get_note_by_token(data_dir: str, token: str) -> Optional[Dict[str, Any]]:
    """
    Get the note info for a share token.
    
    Args:
        data_dir: Path to the data directory
        token: The share token
    
    Returns:
        Dict with 'path' and 'theme', or None if token not found
    """
    tokens = load_tokens(data_dir)
    
    if token in tokens:
        return {
            'path': tokens[token].get('path'),
            'theme': tokens[token].get('theme', 'light')
        }
    
    return None


def get_all_shared_paths(data_dir: str) -> list:
    """
    Get a list of all currently shared note paths.
    Used for displaying share indicators in the UI.
    
    Args:
        data_dir: Path to the data directory
    
    Returns:
        List of note paths that are currently shared
    """
    tokens = load_tokens(data_dir)
    return [info.get('path') for info in tokens.values() if info.get('path')]


def revoke_share_token(data_dir: str, note_path: str) -> bool:
    """
    Revoke (delete) the share token for a note.
    
    Args:
        data_dir: Path to the data directory
        note_path: Path to the note
    
    Returns:
        True if token was revoked, False if not found or error
    """
    with _lock:
        tokens = load_tokens(data_dir)
        
        # Find and remove token for this note
        token_to_remove = None
        for token, info in tokens.items():
            if info.get('path') == note_path:
                token_to_remove = token
                break
        
        if token_to_remove:
            del tokens[token_to_remove]
            if not save_tokens(data_dir, tokens):
                _prune_inaccessible_unsafe(data_dir)
                return False
            _prune_inaccessible_unsafe(data_dir)
            return True

        _prune_inaccessible_unsafe(data_dir)
        return False


def get_share_info(data_dir: str, note_path: str) -> Optional[Dict[str, Any]]:
    """
    Get share information for a note.
    
    Args:
        data_dir: Path to the data directory
        note_path: Path to the note
    
    Returns:
        Dict with token, theme, and created date, or None if not shared
    """
    tokens = load_tokens(data_dir)
    
    for token, info in tokens.items():
        if info.get('path') == note_path:
            return {
                'token': token,
                'theme': info.get('theme', 'light'),
                'created': info.get('created'),
                'shared': True
            }
    
    return {'shared': False}


def _normalize_storage_path(path: str) -> str:
    """Forward slashes, no leading or trailing slash."""
    return path.replace("\\", "/").strip("/")


def _retarget_note_path(path: str, old_prefix: str, new_prefix: str) -> Optional[str]:
    """
    Return the note path after a folder rename, or None if this note is outside the folder.

    `projects` must not match `projects-archive/note.md`. Only an exact path or a
    path under `old_prefix/` moves.
    """
    if path == old_prefix:
        return new_prefix
    marker = old_prefix + "/"
    if path.startswith(marker):
        rest = path[len(marker):]
        return f"{new_prefix}/{rest}" if new_prefix else rest
    return None


def update_token_path(data_dir: str, old_path: str, new_path: str) -> bool:
    """
    Update the path for a token when a note is moved/renamed.
    
    Args:
        data_dir: Path to the data directory
        old_path: Old note path
        new_path: New note path
    
    Returns:
        True if updated, False if not found or error
    """
    with _lock:
        tokens = load_tokens(data_dir)
        
        for token, info in tokens.items():
            if info.get('path') == old_path:
                info['path'] = new_path
                return save_tokens(data_dir, tokens)
        
        return False


def update_token_prefix(data_dir: str, old_prefix: str, new_prefix: str) -> int:
    """
    Rewrite share token paths when a folder is renamed or moved.

    Every token whose note lives in that folder (or a nested folder) keeps its
    token and theme; only the stored path changes. Returns how many tokens were
    updated. An empty old prefix matches nothing, so a bad caller cannot rewrite
    every link in the vault.
    """
    old_prefix = _normalize_storage_path(old_prefix)
    new_prefix = _normalize_storage_path(new_prefix)
    if not old_prefix or old_prefix == new_prefix:
        return 0

    with _lock:
        tokens = load_tokens(data_dir)
        changed = 0
        for info in tokens.values():
            path = info.get("path")
            if not isinstance(path, str) or not path:
                continue
            rewritten = _retarget_note_path(
                _normalize_storage_path(path), old_prefix, new_prefix
            )
            if rewritten is None or rewritten == path:
                continue
            info["path"] = rewritten
            changed += 1
        if not changed:
            return 0
        if not save_tokens(data_dir, tokens):
            return 0
        logger.info(
            "Updated %s share token path(s) for folder move %s -> %s",
            changed,
            old_prefix,
            new_prefix,
        )
        return changed


def delete_token_for_note(data_dir: str, note_path: str) -> bool:
    """
    Delete the share token when a note is deleted.
    Alias for revoke_share_token for clarity.
    """
    return revoke_share_token(data_dir, note_path)
