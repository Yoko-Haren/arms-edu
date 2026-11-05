"""The emails that answer a request from the website once a school's accounts are ready.

They are sent from the ARMS mailbox (settings.DEFAULT_FROM_EMAIL, the Gmail account in EMAIL_HOST_USER) with replies
going to settings.ARMS_CONTACT_EMAIL. No email carries a password. Each account holder gets a one-time setup link at
the account's own address, to choose a password themselves (accounts.views_account.setup_link). The person who wrote
the request, whose address nobody has verified, gets only a note that the school is ready, with the account addresses
masked. So a request written under a false name never reaches a School Head's or a registrar's account.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import escape, linebreaks


def email_ready():
    """Whether this server can send real email (Gmail SMTP with an app password), not just print it."""
    return settings.EMAIL_BACKEND.endswith('smtp.EmailBackend')


def _page(title, body):
    """The ARMS email layout around some HTML."""
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f5f7fb;font-family:'Segoe UI',Arial,sans-serif;color:#2d3748;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;padding:28px 12px;"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e8ecf1;">
  <tr><td style="background:#123499;background-image:linear-gradient(120deg,#123499,#5a67d8 55%,#764ba2);padding:26px 28px;color:#ffffff;">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;opacity:.85;font-weight:600;">ARMS</div>
    <div style="font-size:22px;font-weight:800;margin-top:6px;">{escape(title)}</div>
  </td></tr>
  <tr><td style="padding:26px 28px 8px;">{body}</td></tr>
  <tr><td style="padding:16px 28px 24px;border-top:1px solid #e8ecf1;font-size:12px;color:#718096;line-height:1.6;">
    If something does not work, reply to this email.<br>ARMS, Academic Records Management System
  </td></tr>
</table></td></tr></table></body></html>"""


def _note_html(note):
    return f'<div style="font-size:15px;line-height:1.6;color:#2d3748;margin:0 0 18px;">{linebreaks(escape(note))}</div>' if note else ''


def setup_email(*, name, role, username, school_name, link, days, note=''):
    """(subject, text, html) of one account's setup email: its own link, never a password."""
    school = (school_name or 'ARMS').rstrip('.')
    subject = f'Set up your ARMS account for {school}'
    text = '\n'.join([f'Hello {name},', ''] + ([note, ''] if note else []) + [
        f'Your ARMS account for {school} is ready. You are its {role}.',
        f'You sign in with: {username}', '',
        f'Choose your password here (the link works once, for {days} days):', link, '',
        'If you did not expect this, ignore this email: nothing changes until the link is used.', '',
        'ARMS, Academic Records Management System'])
    body = f"""
    <p style="font-size:15px;margin:0 0 14px;">Hello {escape(name)},</p>
    {_note_html(note)}
    <p style="font-size:15px;line-height:1.6;margin:0 0 16px;">Your ARMS account for {escape(school)} is ready. You are its <b>{escape(role)}</b>, and you sign in with <b style="color:#123499;">{escape(username)}</b>.</p>
    <p style="margin:22px 0;text-align:center;"><a href="{escape(link)}" style="display:inline-block;background:#123499;color:#ffffff;text-decoration:none;font-weight:700;padding:12px 26px;border-radius:10px;font-size:15px;">Choose my password</a></p>
    <p style="font-size:13px;line-height:1.6;color:#4a5568;margin:0 0 18px;">The link works once, for {days} days. If you did not expect this email, ignore it: nothing changes until the link is used.</p>"""
    return subject, text, _page(f'Welcome to ARMS', body)


def ready_email(*, to_name, school_name, accounts, note='', first_time=True):
    """(subject, text, html) of the note to the person who wrote the request: who got a setup link, addresses masked."""
    school = (school_name or 'ARMS').rstrip('.')
    subject = f'{school} is ready in ARMS' if first_time else 'Your ARMS sign-in help'
    lines = [f'Hello {to_name},', ''] + ([note, ''] if note else [])
    lines.append(f'{"ARMS is ready for " + school + "." if first_time else "We sent the account below a new setup link."} '
                 'Each account holder received a link at their own email to choose a password:')
    lines += [f'  {a["role"]}: {a["name"]} ({a["masked"]})' for a in accounts]
    lines += ['', 'The links work once. If one does not arrive, check the spam folder, then reply to this email.', '',
              'ARMS, Academic Records Management System']
    text = '\n'.join(lines)
    rows = ''.join(f'<li style="margin:0 0 6px;"><b>{escape(a["role"])}</b>: {escape(a["name"])} <span style="color:#5a6478;">({escape(a["masked"])})</span></li>'
                   for a in accounts)
    body = f"""
    <p style="font-size:15px;margin:0 0 14px;">Hello {escape(to_name)},</p>
    {_note_html(note)}
    <p style="font-size:15px;line-height:1.6;margin:0 0 12px;">{'ARMS is ready for ' + escape(school) + '.' if first_time else 'We sent the account below a new setup link.'} Each account holder received a link at their own email to choose a password:</p>
    <ul style="font-size:14px;line-height:1.6;color:#2d3748;padding-left:20px;margin:0 0 16px;">{rows}</ul>
    <p style="font-size:13px;line-height:1.6;color:#4a5568;margin:0 0 18px;">The links work once. If one does not arrive, check the spam folder, then reply to this email.</p>
    {'<p style="font-size:13px;line-height:1.6;color:#4a5568;margin:0 0 18px;">Next, the School Head starts the school year, and the registrar enrolls the learners from SF1 and adds the teachers.</p>' if first_time else ''}"""
    return subject, text, _page(f'{school} is ready' if first_time else 'Sign-in help', body)


def _message(to, subject, text, html, connection):
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to], reply_to=[settings.ARMS_CONTACT_EMAIL],
                                     connection=connection)
    message.attach_alternative(html, 'text/html')
    return message


def send_setup_emails(*, setups, requester, school_name, note='', first_time=True):
    """Send every account its own setup email and the requester the masked note, over one connection.
    setups: [{'email', 'name', 'role', 'username', 'link', 'days', 'masked'}]; requester: (email, name) or None."""
    connection = get_connection(fail_silently=False)
    messages = [_message(s['email'], *setup_email(name=s['name'], role=s['role'], username=s['username'], school_name=school_name,
                                                  link=s['link'], days=s['days'], note=note), connection) for s in setups]
    if requester and requester[0].lower() not in {s['email'].lower() for s in setups}:
        messages.append(_message(requester[0], *ready_email(to_name=requester[1], school_name=school_name, accounts=setups,
                                                            note=note, first_time=first_time), connection))
    connection.send_messages(messages)
    return len(messages)
