"""Installation report and capability summary. Pure rendering: no I/O, never logs a user in."""
from datetime import datetime

# These describe public routes an agent can try, not authenticated Reach readiness.
# A successful sample depends on the tool, network exit, content and platform response.
_PUBLIC_ACCESS = {
    'web': "Use the agent's own web search and fetch first; call Reach only for the remaining gaps.",
    'github': 'Public repos, READMEs, issues/PRs and public comments can be read via the web or the public API first.',
    'youtube': 'Public search and descriptions can be read first; native tools can return some comments, subtitles and timestamps.',
    'reddit': 'Public search, post bodies and some comments can be read first; some public JSON samples include comments with parent_id.',
    'twitter': 'Public search snippets and some single-post full texts can be tried first; public embeds do not provide replies.',
    'instagram': 'Public search or profile info can be tried first; the HTML may be only an app shell, captions and comments are not uniformly verified.',
    'facebook': 'Public search snippets can be tried first; post bodies and comments depend on the page and access path.',
    'linkedin': 'Public jobs can be searched and read first; logged-out job samples were obtained on 2026-10-21, not every job is guaranteed readable.',
}

_CAPABILITY_LIMITS = {
    'youtube': ['read returns metadata and the description; there is no separate comments action.',
                'Subtitles depend on available tracks; transcription also needs a configured service and quota, login does not guarantee subtitles.'],
    'reddit': ['read only covers the post and comments it returns; no complete comment tree, no commenting action.',
               'saved lists your own saved posts and needs your login.'],
    'twitter': ['thread only covers the replies actually returned; no complete reply stream, no posting action.',
                'bookmarks lists your own bookmarks and needs your login.'],
    'instagram': ['search finds users; there is no arbitrary post read or separate comments action.',
                  'saved lists your own saved posts (optionally one collection) and needs your login.'],
    'facebook': ['Only search/profile are declared; no separate post read or comment export.'],
    'linkedin': ['Only job search and job description reads are declared; no contacts or feed scraping.'],
}


# Public web options and authenticated Reach actions; detailed limits stay in capability_summary.
_QUICK_ACCESS = {
    'github': ('✅ find projects, read docs, discussions and comments', '—'),
    'youtube': ('✅ find videos, read descriptions and subtitles', '—'),
    'web': ('✅ search and read web pages', '—'),
    'reddit': ('✅ search posts, read bodies and comments', '❌ your saved posts'),
    'twitter': ('✅ search snippets, try reading posts', '❌ search posts, timelines, replies, your bookmarks'),
    'instagram': ('✅ search bios', '❌ find users, profiles, posts, your saved posts'),
    'facebook': ('✅ search snippets', '❌ in-site search, profiles'),
    'linkedin': ('✅ find jobs, read public job details', '❌ in-site search'),
}

_DISPLAY_NAMES = {
    'github': 'GitHub', 'youtube': 'YouTube', 'web': 'Web', 'reddit': 'Reddit',
    'twitter': 'X / Twitter', 'facebook': 'Facebook', 'instagram': 'Instagram', 'linkedin': 'LinkedIn',
}

_STATUS_LABELS = {
    'not_added': 'not added, can be added on demand', 'needs_setup': 'needs setup', 'unverified': 'added, not tested yet',
    'ready': 'verified', 'search_ready': 'only search verified',
    'needs_login': 'the tested action needs login', 'cooldown': 'paused',
    'needs_user_action': 'waiting for the user', 'broken': 'current path broken',
}


def _local_greeting():
    """Use the installing computer's clock and timezone, without a network lookup."""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return 'Good morning. What would you like to look up? ☀️'
    if 12 <= hour < 18:
        return 'Good afternoon. What would you like to look up?'
    if 18 <= hour < 22:
        return 'Good evening. What would you like to look up?'
    return "It's late. What would you like to look up? 🌙"


def _upgrade_lines(result):
    """Only for users coming from 1.5: what was kept, what changed, what is left to do."""
    migration = result.get('migration')
    if not migration:
        return []
    lines = ['**This is an upgrade from an older version (1.5):**', '',
             '- Your keys and settings were kept.',
             '- RSS feeds and Exa web search are no longer separate; searching and reading pages both use "Web".',
             "- Platforms that need a login, such as X, now use Agent Reach's own dedicated browser; on first use it tries to reuse your Chrome login.",
             '- Command-line tools you installed yourself (such as twitter-cli) were left as they were.']
    suggested = [_DISPLAY_NAMES.get(n, n) for n in migration.get('suggested_channels') or []]
    if suggested:
        lines.append('- You used ' + ', '.join(suggested) + ' before; say "add X" to keep using it.')
    old = result.get('old_copies') or {}
    if old.get('removed'):
        lines.append('- The old program was removed, so it cannot conflict with the new one.')
    for item in old.get('failed') or []:
        from .legacy import manual_command
        lines.append(f"- The old program could not be removed; please run: `{manual_command(item)}`")
    return lines + ['']


def installation_report(result):
    """Render one short receipt; keep detailed evidence in the structured summary."""
    def cell(value):
        return str(value).replace('|', r'\|').replace('\n', ' ')

    entries = result['capability_summary']['channels']
    installs = {row['channel']: row for row in result['default_channels']}
    installed = sum(bool(row['ok']) for row in installs.values())
    completed = all(row['ok'] for row in installs.values())
    lines = [
        _local_greeting(), '',
        f"**Agent Reach {result['version']} {'is installed' if completed else 'needs one more step'}.** "
        f'Default features: {installed}/{len(installs)} ready.', '',
        *_upgrade_lines(result),
        '✅ works without login; ❌ try after login; 🔑 needs a key; — no extra login needed.', '',
        '| Platform | Without login | After login |', '|---|---|---|',
    ]
    for entry in sorted(entries, key=lambda item: item['level'] != 'default'):
        name = entry['channel']
        label = _DISPLAY_NAMES.get(name, name)
        item = installs.get(name)
        if entry['level'] == 'default':
            if item and not item['ok']:
                label += ' (install failed: ' + item.get('error', {}).get('code', 'UNKNOWN') + ')'
            elif not item:
                label += ' (removed by the user)'
        elif entry['reach_status'] in {'needs_setup', 'needs_login', 'cooldown', 'needs_user_action', 'broken'}:
            label += ' (' + _STATUS_LABELS[entry['reach_status']] + ')'
        public, login = _QUICK_ACCESS.get(name, (entry['public_access'], '—'))
        lines.append('| ' + ' | '.join(cell(value) for value in (label, public, login)) + ' |')
    verified = [entry for entry in entries if entry['verified_actions']]
    if verified:
        names = '; '.join(_DISPLAY_NAMES.get(entry['channel'], entry['channel']) + ': ' +
                          '/'.join(entry['verified_actions']) for entry in verified)
        lines += ['', 'Tested and passing: ' + names + '. The rest have not been tested online one by one.']
    else:
        lines += ['', 'Not tested online one by one yet.']
    lines += ['Snippets, titles and links come from search engines first; comments are limited to what was read, subtitles must come with the video.', '',
              '**Just say what you want to look up.** Existing logins are reused first; a window opens only when you really need to log in.']
    return '\n'.join(lines) + '\n'


def capability_summary(channels, statuses):
    """Describe public options, declared actions and local evidence without any I/O.

    Management supplies status records so this summary cannot initiate discovery,
    installation, login or a live request. Actions always come from the registry.
    """
    by_name = {entry['channel']: entry for entry in statuses}
    entries = []
    for ch in channels:
        current = by_name.get(ch.name, {})
        names = [action.name for action in ch.actions]
        can_login = bool(ch.login_url or ch.login_argv)
        auth_actions = names if can_login else []
        entries.append({
            'channel': ch.name,
            'level': ch.level,
            'reach_status': current.get('status', 'unverified'),
            'verified_actions': current.get('verified_actions', []),
            'actions': [{'action': action.name, 'detail': action.help} for action in ch.actions],
            'public_access': _PUBLIC_ACCESS.get(ch.name, "Try the agent's own public search and fetch first."),
            'authentication': {
                'when': 'only_if_required',
                'may_enable_actions': [name for name in auth_actions if name in names],
                'note': 'When the chosen path for the remaining task really needs authentication, reuse an authorised session first, then ask the user to log in; verify the action after login.',
            },
            'limits': _CAPABILITY_LIMITS.get(ch.name, []),
        })
    message = ["Use the agent's own public search and fetch first; Reach fills gaps on demand, installation does not require logging in to every platform."]
    for entry in entries:
        actions = '/'.join(action['action'] for action in entry['actions'])
        status_label = _STATUS_LABELS.get(entry['reach_status'], entry['reach_status'])
        limits = ' '.join(entry['limits'])
        message.append(f"{entry['channel']}: {entry['public_access']} Reach {actions} ({status_label}). {limits}")
    return {
        'policy': 'public_first; login_only_for_remaining_task_when_required',
        'evidence_note': 'Public-path notes come from samples and access conditions and do not replace a local Reach check; network errors, 403, timeouts or captchas alone do not prove a login is needed.',
        'channels': entries,
        'message': '\n'.join(message),
    }
