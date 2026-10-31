"""Provider retention wording for Settings, without changing provider settings."""
import json
from . import platform_paths


def describe():
    settings_path = platform_paths.claude_home()/'settings.json'
    values = {}
    problem = False
    # Global values only. Folder overrides and remote/MDM policy cannot be
    # reliably resolved here, and are explicitly qualified in the wording.
    for path in [settings_path, *reversed(platform_paths.claude_managed_settings())]:
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Too large')
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict):
                raise ValueError('Not settings')
            for key, minimum in [('cleanupPeriodDays', 1), ('desktopSessionCleanupPeriodDays', 0)]:
                if key in data:
                    value = data[key]
                    if type(value) is not int or value < minimum:
                        raise ValueError('Invalid retention')
                    values[key] = value
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            problem = True
    if problem:
        claude = 'Claude: retention could not be checked because a settings file is unreadable or invalid.'
    else:
        days = values.get('cleanupPeriodDays', 30)
        desktop = values.get('desktopSessionCleanupPeriodDays', 0)
        claude = (f'Claude Code: cleanupPeriodDays is {days} days in readable global settings '
                  '(default 30). Folder settings or administrator policy can override this. '
                  + (f'Desktop-host transcripts: at least {max(days, desktop)} days.' if desktop
                     else 'Recent Claude versions exempt desktop-host transcripts by default.'))
    from .history_keeper import keeper
    store = keeper()
    status = (f'Kept Claude chats: {len(store.records())}. '
              f'Copy or catalog problems: {len(store.problems)}. '
              'New copies may still be running in the background.')
    return '\n'.join([claude,
        'Codex: no documented automatic age limit for saved threads. Prompt history limits are separate.',
        'Copilot CLI: no documented automatic age limit for saved sessions.',
        'The Chat Place keeps copies of Claude transcripts it sees, including terminal chats. '
        'After the original disappears, the copy is read-only. Copies have no automatic expiry '
        'and are unencrypted. Back Up Now includes them; a copy on this disk still needs a backup.', status])
