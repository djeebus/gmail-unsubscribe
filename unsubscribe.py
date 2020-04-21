from email.mime.text import MIMEText

from googleapiclient.discovery import build
from httplib2 import Http
from oauth2client import file, client, tools
import base64
import bs4
import click
import itertools
import operator
import re
import urllib.parse
import webbrowser

unsubscribed_label = 'Unsubscribed'
subscribed_label = 'Subscription'
inbox_label = 'INBOX'


@click.command()
@click.option('--count', default=10, help='Check this many messages for links')
def cli(count):
    # Track Senders
    seen = set()
    # Set label
    label = 'Unsubscribed'
    # Get Service
    gmail = get_gmail_service()

    inbox_label_id = get_or_create_label_id(gmail, inbox_label)
    unsubscribed_label_id = get_or_create_label_id(gmail, unsubscribed_label)
    subscribed_label_id = get_or_create_label_id(gmail, subscribed_label)

    whitelisted_addresses = set()
    unsubscribed_addresses = set()

    # Get Unsubscribe messages
    message_ids = get_recent_message_ids(gmail, count)
    for message_id in message_ids:
        message = get_message(gmail, message_id)

        subject = get_header_value(message, 'Subject')
        from_email = get_header_value(message, 'From')
        print(f'parsing {subject} from {from_email}')

        if has_label(message, subscribed_label_id):
            whitelisted_addresses.add(from_email)
            continue

        if from_email in whitelisted_addresses:
            print(f'\tmarking "{from_email}" as subscribed')
            modify_labels(gmail, message, add_label_ids=[subscribed_label_id])
            continue

        if has_label(message, unsubscribed_label_id):
            unsubscribed_addresses.add(from_email)
            continue

        if from_email in unsubscribed_addresses:
            print(f'\tmarking "{from_email}" as unsubscribed')
            modify_labels(
                gmail, message,
                add_label_ids=[unsubscribed_label_id],
                remove_label_ids=[inbox_label_id],
            )
            continue

        unsub = should_unsubscribe(subject, from_email)
        if unsub == UNSUB_NO:
            continue

        if unsub == UNSUB_NEVER:
            whitelisted_addresses.add(from_email)
            print(f'\tmarking all "{from_email}" as subscribed')
            batch_modify_labels(
                gmail, from_email,
                add_label_ids=[subscribed_label_id],
            )
            continue

        success = unsubscribe(gmail, message)
        if success:
            print(f'\tmarking all "{from_email}" as unsubscribed')
            batch_modify_labels(
                gmail, from_email,
                add_label_ids=[unsubscribed_label_id],
                remove_label_ids=[inbox_label_id],
            )


def unsubscribe(gmail, message):
    value = get_header_value(message, 'List-Unsubscribe')
    if value:
        success = _unsubscribe_via_list_unsubscribe_header(gmail, value)
        if success:
            return True

    success = _unsubscribe_via_html_link(gmail, message)
    if success:
        return True

    return False


def _find_parts(message):
    payload = message['payload']

    body = payload.get('body')
    if body:
        yield payload

    parts = payload.get('parts')
    if parts:
        yield from parts


def _find_unsubscribe_links(soup):
    links = soup.find_all('a')
    for link in links:
        text = link.text or ''
        text = text.lower()
        if 'unsubscribe' in text:
            yield link.attrs['href']


def _unsubscribe_via_html_link(gmail, message):
    for part in _find_parts(message):
        if part['mimeType'] != 'text/html':
            continue

        html = base64.urlsafe_b64decode(part['body']['data'])
        soup = bs4.BeautifulSoup(html, features='html.parser')
        for unsubscribe_link in _find_unsubscribe_links(soup):
            success = _wait_for_unsubscribe_link(unsubscribe_link)
            if success:
                return True

        return False


def _unsubscribe_via_list_unsubscribe_header(gmail, value):
    links = _parse_unsubscribe_header(value)

    for link in links:
        url = urllib.parse.urlparse(link)
        if url.scheme == 'mailto':
            print('\tsending unsubscribe email ...')
            if _send_unsubscribe_email(gmail, url):
                print('\tsuccess!')
                return True

            print(f'\tfailed to send unsubscribe email')

        if url.scheme in ('http', 'https'):
            success = _wait_for_unsubscribe_link(url.geturl())
            if success:
                return True

        raise NotImplementedError(url.scheme)


def _wait_for_unsubscribe_link(href):
    print('\topening browser to unsubscribe')
    webbrowser.open(href)

    return click.confirm(
        '\tSuccessful?',
        show_default=True, default=True,
    )


default_subject = 'Unsubscribe request'
default_body = 'Please unsubscribe me from this list'


def _send_unsubscribe_email(gmail, mail_to_url):
    query = urllib.parse.parse_qsl(mail_to_url.query)
    query = dict(query)

    message = MIMEText(query.get('body', default_body))
    message['to'] = mail_to_url.path
    message['subject'] = query.get('subject', default_subject)
    message = message.as_string().encode('utf-8')
    message = base64.urlsafe_b64encode(message)

    msg_svc = gmail.users().messages()
    request = msg_svc.send(userId='me', body={'raw': message.decode('utf-8')})
    response = request.execute()
    return True


hdr_re = re.compile('<([^>]+)>')


def _parse_unsubscribe_header(value):
    links = []

    matches = hdr_re.findall(value)
    for link in matches:
        if link.startswith('http'):
            links.insert(0, link)
        else:
            links.append(link)
    return links


def modify_labels(
    gmail, message,
    *, add_label_ids=None, remove_label_ids=None,
):
    if not add_label_ids and not remove_label_ids:
        return

    body = dict()
    if add_label_ids:
        body['addLabelIds'] = add_label_ids
    if remove_label_ids:
        body['removeLabelIds'] = remove_label_ids

    svc = gmail.users().messages()
    request = svc.modify(userId='me', id=message['id'], body=body)
    request.execute()


UNSUB_YES = 'yes'
UNSUB_NO = 'no'
UNSUB_NEVER = 'never'


def should_unsubscribe(subject, from_email):
    text = f'''Do you want to unsubscribe from this email?
    {from_email}: {subject} 
'''
    return click.prompt(
        text, show_default=True, default='yes',
        type=click.Choice([UNSUB_YES, UNSUB_NO, UNSUB_NEVER]),
    )


def get_header_value(message, header_key):
    for header in message['payload']['headers']:
        if header['name'] == header_key:
            return header['value']


def has_label(message, label_id):
    return label_id in message['labelIds']


def get_message(service, message_id):
    messages_svc = service.users().messages()
    request = messages_svc.get(userId='me', id=message_id, format='full')
    response = request.execute()
    return response


def batch_modify_labels(
    gmail, from_email, add_label_ids=None, remove_label_ids=None,
):
    if not add_label_ids and not remove_label_ids:
        return

    msg_svc = gmail.users().messages()

    all_message_ids = _get_messages(gmail, q=f'from:{from_email}')
    for message_ids in chunk(all_message_ids, 1000):
        body = {'ids': message_ids}
        if add_label_ids:
            body['addLabelIds'] = add_label_ids
        if remove_label_ids:
            body['removeLabelIds'] = remove_label_ids

        request = msg_svc.batchModify(userId='me', body=body)
        request.execute()


def _get_messages(service, q=None, max_results=None):
    messages_svc = service.users().messages()
    request = messages_svc.list(
        userId='me',
        maxResults=max_results,
        includeSpamTrash=False,
        q=q
    )
    response = request.execute()

    get_id = operator.itemgetter('id')
    yield from map(get_id, response['messages'])

    while True:
        request = messages_svc.list_next(request, response)
        if request is None:
            return

        response = request.execute()
        yield from map(get_id, response['messages'])


def get_recent_message_ids(service, count):
    return _get_messages(
        service,
        q=' '.join((
            'unsubscribe',
            f'-label:{unsubscribed_label}',
            f'-label:{subscribed_label}',
            f'label:{inbox_label}',
        )),
        max_results=count,
    )


def get_or_create_label_id(service, label_name):
    labels = service.users().labels()
    results = labels.list(userId='me').execute()

    for label in results['labels']:
        if label['name'] == label_name:
            return label['id']

    result = labels.create(userId='me', body={'name': label_name}).execute()
    return result['id']


def get_gmail_service():
    SCOPES = 'https://www.googleapis.com/auth/gmail.modify'
    store = file.Storage('credentials.json')
    creds = store.get()
    if not creds or creds.invalid:
        flow = client.flow_from_clientsecrets('client_secret.json', SCOPES)
        creds = tools.run_flow(flow, store)
    service = build('gmail', 'v1', http=creds.authorize(Http()))
    return service


def chunk(iterable, size):
    args = [iter(iterable)] * size
    return itertools.zip_longest(*args)


if __name__ == '__main__':
    cli()
