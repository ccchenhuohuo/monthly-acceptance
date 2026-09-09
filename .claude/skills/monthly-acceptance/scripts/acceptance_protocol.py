"""Current release identity, independent of historical numeric version ordering."""
VERSION = '0.0.1'
ARCHITECTURE = 'panorama_questions'


def current(value):
    manifest = getattr(value, 'manifest', value)
    policy = manifest.get('policy', {})
    return (manifest.get('architecture') == ARCHITECTURE
            or policy.get('architecture') == ARCHITECTURE
            or manifest.get('method_version') == VERSION)


def protocol(value, legacy):
    """New records use the release version; frozen records keep their own format."""
    return VERSION if current(value) else legacy
