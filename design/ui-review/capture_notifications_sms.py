"""Capture production Qt Widgets with isolated fictional Notifications/SMS data.

Run with the existing .venv and a private Xvfb display; no core, credentials or network.
Only the new notifications-sms evidence directory is written.
"""
import hashlib
from html import escape
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone

FIXTURE = tempfile.TemporaryDirectory(prefix='pbdesk-notifications-sms-')
os.environ['PBDESK_APPLICATION_DIR'] = str(Path(FIXTURE.name) / 'app')
os.environ['PBDESK_STATE_DIR'] = str(Path(FIXTURE.name) / 'state')
os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
if os.environ['QT_QPA_PLATFORM'] != 'offscreen' and os.environ.get('PBDESK_CAPTURE_PRIVATE_DISPLAY') != '1':
    raise RuntimeError('Use a private Xvfb display with PBDESK_CAPTURE_PRIVATE_DISPLAY=1, or QT_QPA_PLATFORM=offscreen.')
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QLabel, QTextEdit, QSystemTrayIcon
from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.actions import ComposeDialog, ReviewDialog

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'notifications-sms'
OUT.mkdir(parents=True, exist_ok=True)
ACCOUNT = 'fictional-ui-account'
PHONE = 'fictional-phone-01'
TIME = '2026-11-14T08:00:00+00:00'
DEVICES = [
    {'id': PHONE, 'nickname': 'Personal phone', 'manufacturer': 'Example', 'model': 'Phone One', 'type': 'android', 'active': True, 'can_push': True, 'can_sms': True, 'encryption': {'state': 'verified', 'mode': 'shared'}, 'history': {'count': 48, 'last_success_at': 1794643200}},
    {'id': 'fictional-workstation', 'nickname': 'Linux workstation', 'manufacturer': None, 'model': 'pbdesk Linux', 'type': 'desktop', 'active': True, 'can_push': True, 'can_sms': False},
    {'id': 'fictional-work-phone', 'nickname': 'Work phone', 'manufacturer': 'Example', 'model': 'Phone Two', 'type': 'android', 'active': True, 'can_push': True, 'can_sms': True},
]
CAP = {'account_id': ACCOUNT, 'provider_state': 'connected', 'encryption_state': 'ready', 'encrypted_pending': 0, 'send_ready': True, 'send_unavailable_reason': None,
    'features': {'devices': True, 'send': True, 'send_sms': True, 'sms_history': True, 'device_management': True}, 'push_sync': {'state': 'ready', 'last_success_at': 1794643200}}
ITEMS = [
    {'id': 1, 'type': 'mirror', 'title': 'Calendar — Planning session', 'body': 'Your planning session starts in 15 minutes.\n\nFictional notification for UI review.', 'source_device_id': PHONE, 'decrypted': True, 'created_at': TIME},
    {'id': 2, 'type': 'link', 'title': 'Design notes', 'body': 'A reference for our next discussion.', 'url': 'https://example.com/design', 'source_device_id': 'fictional-workstation', 'created_at': TIME},
    {'id': 3, 'type': 'note', 'title': 'Weekend checklist', 'body': 'Pick up the parcel\nCharge the headphones\nBack up the laptop', 'source_device_id': PHONE, 'created_at': TIME},
]

def action(identity=7, kind='sms', state='pending_approval', **changes):
    value = dict(id=identity, account_id=ACCOUNT, revision=1, fingerprint='a' * 64, kind=kind, transport='provider_plaintext', device_id=PHONE if kind == 'sms' else 'fictional-workstation', recipient='+12025550123' if kind == 'sms' else None,
        title='' if kind == 'sms' else 'Planning notes', body='Can we move our meeting to 3:30?', url='https://example.com/planning' if kind == 'link' else None, requested_by='desktop', state=state, created_at=TIME, updated_at=TIME, expires_at='2026-11-14T09:00:00+00:00', upstream_id=None, error_code=None, error_message=None)
    value.update(changes)
    return value

MESSAGES = [
    {'id': 's1', 'account_id': ACCOUNT, 'device_id': PHONE, 'conversation_id': 'studio', 'message_id': 's1', 'direction': 'incoming', 'name': 'Alex · Studio', 'addresses': ['+12025550123'], 'timestamp': 1794642000, 'body': 'The studio opens at 9. Can you bring the sketches?'},
    {'id': 's2', 'account_id': ACCOUNT, 'device_id': PHONE, 'conversation_id': 'studio', 'message_id': 's2', 'direction': 'outgoing', 'name': 'Alex · Studio', 'addresses': ['+12025550123'], 'timestamp': 1794642300, 'body': 'Yes, I will bring the sketches. See you there.'},
    {'id': 's3', 'account_id': ACCOUNT, 'device_id': PHONE, 'conversation_id': 'studio', 'message_id': 's3', 'direction': 'incoming', 'name': 'Alex · Studio', 'addresses': ['+12025550123'], 'timestamp': 1794642600, 'body': 'Great! We can review the new layout together.'},
    {'id': 's4', 'account_id': ACCOUNT, 'device_id': PHONE, 'conversation_id': 'walking', 'message_id': 's4', 'direction': 'incoming', 'name': 'Sam', 'addresses': ['+12025550124'], 'timestamp': 1794638000, 'body': 'See you at the old bridge at 5.'},
    {'id': 's5', 'account_id': ACCOUNT, 'device_id': PHONE, 'conversation_id': 'parcel', 'message_id': 's5', 'direction': 'incoming', 'addresses': ['+12025550125'], 'timestamp': 1794637000, 'body': 'Your parcel is ready for collection.'},
]

class FixtureWorker(QObject):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    result = pyqtSignal(dict)
    stopped_all = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.requests = []
    def request(self, *args):
        self.requests.append(args)
    def stop(self):
        pass
    def isRunning(self):
        return True

app = QApplication([])
app.setApplicationName('pbdesk fictional UI review')
app.setQuitOnLastWindowClosed(False)
assert app.platformName() in {'xcb', 'offscreen'}, 'Captures must use a private Xvfb display or offscreen rendering, never the user desktop.'
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)
class InputGuard(QObject):
    def eventFilter(self, watched, event):
        return event.type() in {QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel, QEvent.Type.ContextMenu}
guard = InputGuard()
app.installEventFilter(guard)
worker = FixtureWorker()
window = MainWindow(worker)
window.resize(1280, 850)
window.show()
page = window.sms_page
SCREENS = []

def snapshot(actions=()):
    return {'items': ITEMS, 'web': {'enabled': False, 'url': '', 'host': '127.0.0.1', 'port': 8766, 'error': None}, 'capabilities': CAP,
        'devices': {'account_id': ACCOUNT, 'devices': DEVICES, 'synced_at': TIME}, 'actions': list(actions), 'instance_id': 'fictional-instance',
        'sms_device_id': PHONE, 'sms': {'account_id': ACCOUNT, 'messages': MESSAGES, 'sync': {'state': 'ready', 'last_success_at': 1794643200}}}

def timeline_text():
    return '\n'.join(widget.text() if isinstance(widget, QLabel) else widget.toPlainText() for widget in page.timeline_content.findChildren((QLabel, QTextEdit)))

def assert_phone():
    assert page.account_id == ACCOUNT
    assert page.selected_device_id == PHONE and page.phone.currentData() == PHONE
    assert page.threads.count() >= 3
    assert len(page.sms_cache[PHONE]['messages']) == 5

def assert_chat():
    assert_phone()
    assert page.current_thread == ('history', 'studio')
    text = timeline_text()
    assert 'The studio opens at 9.' in text
    assert 'Yes, I will bring the sketches.' in text


def assert_new():
    assert_phone()
    assert page.current_thread[0] == 'new'
    assert 'The studio opens at 9.' not in timeline_text()
    assert 'Yes, I will bring the sketches.' not in timeline_text()


def capture(widget, filename, title, verify=assert_phone):
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    time.sleep(.12)
    app.processEvents()
    verify()
    assert widget.grab().save(str(OUT / filename))
    verify()
    SCREENS.append({'file': filename, 'title': title, 'width': widget.width(), 'height': widget.height(), 'sha256': hashlib.sha256((OUT / filename).read_bytes()).hexdigest()})

try:
    worker.snapshot.emit(snapshot())
    page.phone.setCurrentIndex(page.phone.findData(PHONE))
    worker.connection.emit('Connected to private core · fictional data')
    window.navigate(0)
    capture(window, '01-notifications.png', 'Notifications · source filter and selected detail')
    window.navigate(1)
    page._activate_thread(('history', 'studio'), True)
    capture(window, '02-sms-conversation.png', 'SMS · received and sent bubbles in one conversation', assert_chat)
    page.composer.setPlainText('I have the revised sketches ready. Shall we meet at 3:30?')
    capture(window, '03-sms-draft.png', 'SMS · unsaved reply beneath the timeline', assert_chat)
    page.composer.clear()
    worker.snapshot.emit(snapshot([action()]))
    capture(window, '04-sms-pending.png', 'SMS · pending review request is distinct from sent history', assert_chat)
    worker.snapshot.emit(snapshot([action(state='uncertain', error_code='upstream_outcome_unknown', error_message='Provider result could not be confirmed. No automatic retry.')]))
    capture(window, '05-sms-uncertain.png', 'SMS · uncertain request with no automatic retry', assert_chat)
    page.start_new()
    capture(window, '06-sms-new-message.png', 'SMS · inline new message with explicit recipient', assert_new)
    page.recipient.setText('+12025550126')
    page.composer.setPlainText('Hi, is the workshop still scheduled for Saturday?')
    capture(window, '07-sms-new-draft.png', 'SMS · new conversation draft with explicit phone and number', assert_new)
    actions = [action(), action(8, 'link', 'upstream_accepted', upstream_id='fictional-acceptance'), action(9, 'note', 'uncertain'), action(10, 'sms', 'failed'), action(11, 'note', 'cancelled')]
    worker.snapshot.emit(snapshot(actions))
    window.show_activity()
    capture(window.activity, '08-message-activity.png', 'Message activity · all saved Note, Link and SMS states')
    window.activity.reject()
    review = ReviewDialog(worker, actions[0], DEVICES, CAP, window)
    review.show()
    capture(review, '09-review-exact-message.png', 'Exact review · destination, expiry and unchecked consent')
    review.advanced_toggle.setChecked(True)
    capture(review, '10-review-advanced.png', 'Exact review · revision and fingerprint')
    review.reject()
    uncertain = ReviewDialog(worker, actions[2], DEVICES, CAP, window)
    uncertain.show()
    capture(uncertain, '11-review-uncertain.png', 'Message activity · uncertain action review')
    uncertain.reject()
    window.navigate(2)
    capture(window, '12-devices.png', 'Devices · retained registration and encryption inspection')
    window.navigate(0)
    window.compose()
    compose = window.dialogs[-1]
    compose.device.setCurrentIndex(compose.device.findData('fictional-workstation'))
    compose.title.setText('Planning notes')
    compose.body.setPlainText('The draft is ready for review. Saving sends nothing.')
    capture(compose, '13-compose-note.png', 'Notifications · Note or Link saved for separate review')
    compose.reject()
    assert not any(command in {'create_action', 'approve_action', 'cancel_action', 'edit_action', 'web', 'shutdown', 'add_device', 'encryption'} for command, *_ in worker.requests)
    manifest = {'captured_at': datetime.now(timezone.utc).isoformat(), 'data': 'fictional fixtures only', 'native_platform': app.platformName(), 'capture': 'Actual production Qt6 QWidget.grab on ' + app.platformName(), 'live_core_touched': False, 'credentials_read': False, 'fixture_phone': PHONE, 'fixture_messages': len(MESSAGES), 'screens': SCREENS,
        'source_sha256': {str(path.relative_to(ROOT.parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [ROOT.parent.parent / 'src/pbdesk/desktop' / name for name in ('app.py', 'chat.py', 'activity.py', 'actions.py', 'worker.py', 'theme.py')]}}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    cards = ''.join('<article><a href="' + escape(s['file']) + '"><img src="' + escape(s['file']) + '" alt="' + escape(s['title']) + '"></a><h2>' + escape(s['title']) + '</h2></article>' for s in SCREENS)
    (OUT / 'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>pbdesk Notifications and SMS</title><style>body{font:15px system-ui;background:#faf9f6;color:#24343b;margin:0}header{padding:30px 5%;background:#20383e;color:white}main{padding:28px 5%;display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:24px}article{background:white;border:1px solid #d5dfd7;border-radius:6px;padding:12px}img{width:100%;height:280px;object-fit:contain;background:#f1f3ed}h2{font-size:16px}p{line-height:1.6;max-width:900px}</style><header><h1>pbdesk · Notifications and SMS</h1><p>Actual Qt6 Widgets on a private display. All names, numbers and messages are fictional. Notifications, unified SMS conversations, inline drafts, separate message activity and exact review. The fixture worker never connects to a core, provider or credential store. Select an image for full resolution.</p></header><main>' + cards + '</main></html>')
    print(json.dumps({'screens': len(SCREENS), 'gallery': str(OUT / 'index.html'), 'platform': app.platformName(), 'live_core_touched': False}))
finally:
    window.exit_desktop()
    app.processEvents()
    FIXTURE.cleanup()
