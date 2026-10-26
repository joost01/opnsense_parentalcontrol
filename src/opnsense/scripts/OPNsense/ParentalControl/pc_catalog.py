"""
Built-in catalogue of well-known apps/services and SafeSearch rewrites.

Used by backends that have no service list of their own (Unbound) and to recognise apps in query logs
(Pi-hole). Domains cover the domain and all of its subdomains. The lists are deliberately modest: they are
meant for parental control basics, not for exhaustive blocking.
"""

APPS = {
    # social media
    'Facebook': ['facebook.com', 'fbcdn.net', 'facebook.net', 'fb.com'],
    'Instagram': ['instagram.com', 'cdninstagram.com'],
    'TikTok': ['tiktok.com', 'tiktokcdn.com', 'tiktokv.com', 'musical.ly'],
    'Snapchat': ['snapchat.com', 'sc-cdn.net'],
    'X (Twitter)': ['twitter.com', 'x.com', 'twimg.com', 't.co'],
    'Pinterest': ['pinterest.com', 'pinimg.com'],
    'Reddit': ['reddit.com', 'redd.it', 'redditmedia.com', 'redditstatic.com'],
    'Tumblr': ['tumblr.com'],
    # chat and messaging
    'WhatsApp': ['whatsapp.com', 'whatsapp.net'],
    'Telegram': ['telegram.org', 't.me', 'telegram.me'],
    'Discord': ['discord.com', 'discord.gg', 'discordapp.com', 'discordapp.net'],
    'Signal': ['signal.org'],
    'Viber': ['viber.com'],
    'Skype': ['skype.com'],
    # games
    'Steam': ['steampowered.com', 'steamcommunity.com', 'steamstatic.com', 'steamcontent.com'],
    'Epic Games': ['epicgames.com', 'unrealengine.com', 'fortnite.com'],
    'Roblox': ['roblox.com', 'rbxcdn.com'],
    'Minecraft': ['minecraft.net', 'mojang.com'],
    'Blizzard': ['blizzard.com', 'battle.net'],
    'Riot Games': ['riotgames.com', 'leagueoflegends.com'],
    # video and streaming
    'YouTube': ['youtube.com', 'googlevideo.com', 'ytimg.com', 'youtu.be', 'youtube-nocookie.com'],
    'Netflix': ['netflix.com', 'nflxvideo.net', 'nflximg.net', 'nflxext.com'],
    'Disney+': ['disneyplus.com', 'disney-plus.net', 'bamgrid.com'],
    'Twitch': ['twitch.tv', 'ttvnw.net', 'jtvnw.net'],
    'Prime Video': ['primevideo.com'],
    'Max': ['max.com', 'hbomax.com'],
    'Vimeo': ['vimeo.com', 'vimeocdn.com'],
    # dating
    'Tinder': ['tinder.com', 'gotinder.com'],
    'Bumble': ['bumble.com'],
}

CATEGORIES = {
    'social': ['Facebook', 'Instagram', 'TikTok', 'Snapchat', 'X (Twitter)', 'Pinterest', 'Reddit', 'Tumblr'],
    'messaging': ['WhatsApp', 'Telegram', 'Discord', 'Signal', 'Viber', 'Skype'],
    'games': ['Steam', 'Epic Games', 'Roblox', 'Minecraft', 'Blizzard', 'Riot Games'],
    'video': ['YouTube', 'Netflix', 'Disney+', 'Twitch', 'Prime Video', 'Max', 'Vimeo'],
    'dating': ['Tinder', 'Bumble'],
}

GOOGLE_TLDS = ['com', 'nl', 'de', 'fr', 'be', 'es', 'it', 'at', 'ch', 'pl', 'pt', 'se', 'dk', 'no', 'fi', 'ie',
               'ca', 'co.uk', 'com.au', 'co.nz', 'com.br', 'co.in', 'co.za', 'com.mx', 'co.jp']

# host -> CNAME target that forces the safe mode of the search engine
SAFESEARCH = {'www.bing.com': 'strict.bing.com', 'duckduckgo.com': 'safe.duckduckgo.com',
              'pixabay.com': 'safesearch.pixabay.com'}
SAFESEARCH.update({'www.google.' + tld: 'forcesafesearch.google.com' for tld in GOOGLE_TLDS})
SAFESEARCH.update({host: 'restrict.youtube.com' for host in
                   ('www.youtube.com', 'm.youtube.com', 'youtubei.googleapis.com', 'youtube.googleapis.com',
                    'www.youtube-nocookie.com')})


def category_domains(categories):
    """Sorted list of all domains of the given categories."""
    domains = set()
    for category in categories:
        for app in CATEGORIES.get(category, ()):
            domains.update(APPS[app])
    return sorted(domains)


def domain_to_app():
    """{domain: app name} for recognising apps in query logs."""
    return {domain: app for app, domains in APPS.items() for domain in domains}


def covered(host, domains):
    """True if host equals one of the domains or is a subdomain of one of them."""
    labels = host.lower().rstrip('.').split('.')
    return any('.'.join(labels[i:]) in domains for i in range(len(labels) - 1 if len(labels) > 1 else 1))
