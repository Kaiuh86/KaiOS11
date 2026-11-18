"""KaiOS 11 local account registry and account-specific user file storage.

This is an application-level login, not filesystem encryption or Windows security.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

DATA_HOME = Path.home() / 'KaiOS11'
ACCOUNTS_FILE = DATA_HOME / 'accounts.json'
PROFILES_DIR = DATA_HOME / 'Accounts'
MAX_ACCOUNTS = 5
ROUNDS = 310_000


def profile_directory(account):
    account_id = account.get('id', '')
    if not re.fullmatch(r'[0-9a-f]{24}', account_id):
        raise ValueError('Invalid account identifier')
    return PROFILES_DIR / account_id


def _safe_write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def _copy_old_files(profile):
    """Copy legacy single-user files, never delete or move their originals."""
    for folder_name in ('Desktop', 'Documents', 'Photos', 'Downloads', 'Wallpapers', 'Mail'):
        src = DATA_HOME / folder_name
        target = profile / folder_name
        if not src.is_dir():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            destination = target / item.name
            if destination.exists():
                continue
            if item.is_dir():
                shutil.copytree(item, destination)
            elif item.is_file():
                shutil.copy2(item, destination)
    for filename in ('installed_apps.json', 'appearance_settings.json',
                     'game_scores.json', 'desktop_layout.json'):
        src = DATA_HOME / filename
        dst = profile / filename
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
    # Start the migrated account on the requested red/blue default while
    # retaining all older wallpaper image files and legacy settings backup.
    appearance_file = profile / 'appearance_settings.json'
    if appearance_file.is_file():
        try:
            appearance = json.loads(appearance_file.read_text(encoding='utf-8'))
            if isinstance(appearance, dict):
                appearance.pop('wallpaper', None)
                _safe_write_json(appearance_file, appearance)
        except (OSError, ValueError, TypeError):
            # Keep the original copied settings intact if malformed.
            pass


class AccountStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else ACCOUNTS_FILE
        # Remember whether this computer previously had an account registry.
        # Re-creating an account after deleting the last one must NOT import
        # legacy single-user data as though it were the first-ever launch.
        self.had_registry_file = self.path.is_file()
        if self.had_registry_file:
            # Fail closed on malformed data: don't overwrite existing accounts.
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('accounts'), list):
                raise ValueError('Account file has an invalid format')
            self.accounts = data['accounts']
        else:
            self.accounts = []

    def create(self, username, password, confirmation=None, import_old=False):
        username = username.strip()
        if len(self.accounts) >= MAX_ACCOUNTS:
            raise ValueError('KaiOS supports a maximum of five accounts.')
        if not 2 <= len(username) <= 25 or any(ord(c) < 32 for c in username):
            raise ValueError('Username must be between 2 and 25 characters.')
        if any(u['username'].casefold() == username.casefold() for u in self.accounts):
            raise ValueError('This username already exists. Choose another.')
        if not 4 <= len(password) <= 128:
            raise ValueError('Password must have at least 4 characters (maximum 128).')
        if confirmation is not None and password != confirmation:
            raise ValueError('The two passwords do not match.')

        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ROUNDS)
        account = {
            'id': secrets.token_hex(12),
            'username': username,
            'salt': salt.hex(),
            'password_hash': digest.hex(),
            'iterations': ROUNDS,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        profile = profile_directory(account)
        for name in ('Desktop', 'Documents', 'Photos', 'Downloads', 'Wallpapers', 'Mail'):
            (profile / name).mkdir(parents=True, exist_ok=True)
        # When upgrading, import the former single-user files only for first account.
        if import_old and not self.accounts and not self.had_registry_file:
            _copy_old_files(profile)
        self.accounts.append(account)
        _safe_write_json(self.path, {'version': 1, 'accounts': self.accounts})
        self.had_registry_file = True
        return account

    def authenticate(self, username, password):
        # Compare full derived digest without exposing stored plain-text secrets.
        for account in self.accounts:
            if account['username'].casefold() != username.strip().casefold():
                continue
            try:
                salt = bytes.fromhex(account['salt'])
                expected = bytes.fromhex(account['password_hash'])
                iterations = int(account.get('iterations', ROUNDS))
                if not 100_000 <= iterations <= 2_000_000:
                    return None
                provided = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
                if hmac.compare_digest(expected, provided):
                    return account
            except (TypeError, ValueError, KeyError):
                return None
        return None

    def delete_account(self, username, password, *, permanently_delete_files=False):
        """Remove a verified account; normally archive its profile for recovery.

        Uses a reversible same-volume profile rename before updating accounts.json.
        If writing the registry fails, the profile is restored. Removing the
        final account leaves an empty registry so KaiOS can offer Create Account.
        """
        account = self.authenticate(username, password)
        if account is None:
            raise ValueError('Incorrect password. The account was not deleted.')
        account_id = account['id']
        profile = profile_directory(account)
        archive = None
        if profile.is_symlink():
            raise ValueError('Unsafe account folder link; refusing to delete the account.')
        if profile.exists():
            if not profile.is_dir():
                raise ValueError('Account profile path is not a directory.')
            archive_root = DATA_HOME / 'DeletedAccounts'
            if archive_root.is_symlink():
                raise ValueError('Unsafe DeletedAccounts folder link.')
            archive_root.mkdir(parents=True, exist_ok=True)
            name = account_id + '_' + datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            archive = archive_root / name
            while archive.exists():
                archive = archive_root / (name + '_' + secrets.token_hex(3))
            profile.rename(archive)

        updated = [a for a in self.accounts if a['id'] != account_id]
        try:
            _safe_write_json(self.path, {'version': 1, 'accounts': updated})
        except Exception:
            if archive is not None and archive.exists():
                archive.rename(profile)
            raise
        self.accounts = updated
        self.had_registry_file = True

        if archive is None:
            return 'The account was deleted. No account files were found.'
        if permanently_delete_files:
            try:
                shutil.rmtree(archive)
                return 'The account and its stored KaiOS files were permanently deleted.'
            except OSError as exc:
                return ('The account was removed, but its files could not be fully deleted. '
                        'Remaining files may be in: ' + str(archive) + '\nError: ' + str(exc))
        return ('The account was deleted. Its files were saved as a recovery backup in:\n'
                + str(archive))

    def usernames(self):
        return [account['username'] for account in self.accounts]
