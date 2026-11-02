"""Region, division, district and municipality or city of a school: the header every DepEd school form carries.

ARMS keeps them per school in the DepEd configuration store (one JSON entry per school, visible to
the administrator). They are learned from the SF1 the registrar imports and can be corrected on
the School Forms page. A value is only replaced by a non-empty one.
"""
import base64
import io
import json
import re

KEY = 'SCHOOL_FORM_HEADER:{}'
LOGO_KEY = 'SCHOOL_FORM_LOGO:{}'
FIELDS = ('region', 'division', 'district', 'place')
_NAMED_REGIONS = ('NCR', 'CAR', 'CARAGA', 'BARMM', 'MIMAROPA', 'NIR')


def get(school):
    """{'region': 'XI', 'division': 'Panabo City', 'district': '...', 'place': 'Panabo City, Davao del Norte'} ('' when not known)."""
    from configuration.models import DepEdConfiguration
    row = DepEdConfiguration.objects.filter(config_key=KEY.format(school.school_id)).first()
    try:
        stored = json.loads(row.config_value) if row else {}
    except ValueError:
        stored = {}
    return {f: str(stored.get(f) or '') for f in FIELDS}


def remember(school, values, user=None):
    """Keep the non-empty region / division / district of values; returns what is stored."""
    from configuration.models import DepEdConfiguration
    current = get(school)
    new = dict(current)
    for f in FIELDS:
        value = re.sub(r'\s+', ' ', str(values.get(f) or '')).strip()
        if f == 'region':
            value = region_code(value)
        if value:
            new[f] = value[:100]
    if new != current:
        DepEdConfiguration.objects.update_or_create(
            config_key=KEY.format(school.school_id),
            defaults={'config_value': json.dumps(new), 'config_type': 'JSON', 'category': 'Division_Office',
                      'description': f'Region, division and district printed on the school forms of {school.school_name}.',
                      'updated_by': user if user is not None and user.is_authenticated else None})
    return new


def save(school, values, user=None):
    """Set region, division and district as given (the School Forms page): an empty field clears it."""
    from configuration.models import DepEdConfiguration
    new = {f: re.sub(r'\s+', ' ', str(values.get(f) or '')).strip()[:100] for f in FIELDS}
    new['region'] = region_code(new['region'])
    DepEdConfiguration.objects.update_or_create(
        config_key=KEY.format(school.school_id),
        defaults={'config_value': json.dumps(new), 'config_type': 'JSON', 'category': 'Division_Office',
                  'description': f'Region, division and district printed on the school forms of {school.school_name}.',
                  'updated_by': user if user is not None and user.is_authenticated else None})
    return new


def logo(school):
    """The school's logo for its forms, PNG bytes, or None."""
    from configuration.models import DepEdConfiguration
    row = DepEdConfiguration.objects.filter(config_key=LOGO_KEY.format(school.school_id)).first()
    try:
        return base64.b64decode(row.config_value) if row and row.config_value else None
    except ValueError:
        return None


def save_logo(school, uploaded, user=None):
    """Keep a picture as the school's logo: square PNG, small enough to go on every report card."""
    from configuration.models import DepEdConfiguration
    from PIL import Image
    image = Image.open(uploaded).convert('RGBA')
    image.thumbnail((360, 360))
    square = Image.new('RGBA', (360, 360), (255, 255, 255, 0))
    square.paste(image, ((360 - image.width) // 2, (360 - image.height) // 2))
    out = io.BytesIO()
    square.save(out, 'PNG', optimize=True)
    DepEdConfiguration.objects.update_or_create(
        config_key=LOGO_KEY.format(school.school_id),
        defaults={'config_value': base64.b64encode(out.getvalue()).decode('ascii'), 'config_type': 'String', 'category': 'Division_Office',
                  'description': f'Logo printed on the report cards of {school.school_name}.',
                  'updated_by': user if user is not None and user.is_authenticated else None})


def region_code(text):
    """'Region XI', 'REGION 11', 'XI' -> 'XI'; 'NCR' stays; '' when empty."""
    t = re.sub(r'\s+', ' ', (text or '').strip()).upper()
    t = re.sub(r'^REGION\s*', '', t).strip(' -:')
    t = re.sub(r'\s*\(.*\)$', '', t)                       # "XI (DAVAO REGION)"
    return t


def region_label(code):
    """How the forms write a region: 'Region XI', 'Region IV-A', 'NCR'."""
    c = region_code(code)
    if not c:
        return ''
    if c in _NAMED_REGIONS or not re.fullmatch(r'[IVX]+(-[AB])?|\d{1,2}(-[AB])?', c):
        return c
    return f'Region {c}'
