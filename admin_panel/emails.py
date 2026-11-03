"""The email that answers a request from the website with the school's sign-in details.

It is sent from the ARMS mailbox (settings.DEFAULT_FROM_EMAIL, the Gmail account in EMAIL_HOST_USER) with replies going
to settings.ARMS_CONTACT_EMAIL. Each account in it has a fresh temporary password that its owner must change at the
first sign-in, so the email stops being a key once they have signed in.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape, linebreaks


def email_ready():
    """Whether this server can send real email (Gmail SMTP with an app password), not just print it."""
    return settings.EMAIL_BACKEND.endswith('smtp.EmailBackend')


def _rows_html(cards):
    rows = []
    for c in cards:
        rows.append(
            '<tr>'
            f'<td style="padding:12px 14px;border-top:1px solid #e8ecf1;"><div style="font-size:12px;color:#5a6478;text-transform:uppercase;letter-spacing:.04em;font-weight:600;">{escape(c["role"])}</div>'
            f'<div style="font-size:15px;font-weight:700;color:#1a1a2e;">{escape(c["name"])}</div></td>'
            f'<td style="padding:12px 14px;border-top:1px solid #e8ecf1;"><div style="font-size:12px;color:#5a6478;">Email</div>'
            f'<div style="font-size:14px;font-weight:600;color:#123499;word-break:break-all;">{escape(c["username"])}</div>'
            f'<div style="font-size:12px;color:#5a6478;margin-top:6px;">Temporary password</div>'
            f'<div style="font-family:Consolas,Menlo,monospace;font-size:15px;font-weight:700;color:#1d4721;letter-spacing:.03em;">{escape(c["password"])}</div></td>'
            '</tr>'
        )
    return ''.join(rows)


def sign_in_email(*, to_name, school_name, cards, note, signin_url, first_time=True):
    """(subject, text, html) of the sign-in email."""
    plural = len(cards) != 1
    subject = f'Your ARMS sign-in details for {school_name}' if school_name else 'Your ARMS sign-in details'
    lines = [f'Hello {to_name},', '']
    if note:
        lines += [note, '']
    lines += [f'Here {"are the accounts" if plural else "is the account"} for {school_name or "ARMS"}:', '']
    for c in cards:
        lines += [f'{c["role"]}: {c["name"]}', f'  Email: {c["username"]}', f'  Temporary password: {c["password"]}', '']
    lines += [
        f'Sign in at {signin_url}',
        'ARMS asks for a new password at the first sign-in. The temporary one stops working after that.',
        '',
        'If something does not work, reply to this email.',
        '',
        'ARMS, Academic Records Management System',
    ]
    text = '\n'.join(lines)

    note_html = f'<div style="font-size:15px;line-height:1.6;color:#2d3748;margin:0 0 18px;">{linebreaks(escape(note))}</div>' if note else ''
    html = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f5f7fb;font-family:'Segoe UI',Arial,sans-serif;color:#2d3748;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e8ecf1;">
  <tr><td style="background:#123499;background-image:linear-gradient(120deg,#123499,#5a67d8 55%,#764ba2);padding:26px 28px;color:#ffffff;">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.85;font-weight:600;">ARMS</div>
    <div style="font-size:22px;font-weight:800;margin-top:6px;">{escape(school_name or 'Your ARMS account')} is ready</div>
  </td></tr>
  <tr><td style="padding:26px 28px 8px;">
    <p style="font-size:15px;margin:0 0 14px;">Hello {escape(to_name)},</p>
    {note_html}
    <p style="font-size:15px;line-height:1.6;margin:0 0 16px;">Here {"are the accounts" if plural else "is the account"} for {escape((school_name or 'ARMS').rstrip('.'))}. Each person signs in with their own email and temporary password.</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e8ecf1;border-radius:12px;border-collapse:separate;overflow:hidden;">{_rows_html(cards)}</table>
    <p style="margin:22px 0;text-align:center;"><a href="{escape(signin_url)}" style="display:inline-block;background:#123499;color:#ffffff;text-decoration:none;font-weight:700;padding:12px 26px;border-radius:10px;font-size:15px;">Sign in to ARMS</a></p>
    <ol style="font-size:14px;line-height:1.7;color:#4a5568;padding-left:20px;margin:0 0 18px;">
      <li>Open the sign-in page and type the email and temporary password.</li>
      <li>Choose your own password when ARMS asks. The temporary one stops working.</li>
      {'<li>The School Head starts the school year; the registrar enrolls the learners from SF1 and adds the teachers.</li>' if first_time else ''}
    </ol>
  </td></tr>
  <tr><td style="padding:16px 28px 24px;border-top:1px solid #e8ecf1;font-size:12px;color:#718096;line-height:1.6;">
    Keep this email private: it holds passwords. If something does not work, reply to this email.<br>ARMS, Academic Records Management System
  </td></tr>
</table></td></tr></table></body></html>"""
    return subject, text, html


def send_sign_in_email(*, to_email, to_name, school_name, cards, note, signin_url, first_time=True):
    subject, text, html = sign_in_email(to_name=to_name, school_name=school_name, cards=cards, note=note, signin_url=signin_url,
                                        first_time=first_time)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to_email], reply_to=[settings.ARMS_CONTACT_EMAIL])
    message.attach_alternative(html, 'text/html')
    message.send(fail_silently=False)
