"""Capture the current product UI using fictional data, with no live core access."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time

os.environ['QT_QPA_PLATFORM'] = 'wayland'
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QSystemTrayIcon
from pbdesk.desktop.app import MainWindow
from pbdesk.desktop.actions import ComposeDialog, ReviewDialog

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'current'
OUT.mkdir(parents=True, exist_ok=True)
SCREENS = []


class ScreenshotWorker(QObject):
    snapshot = pyqtSignal(dict)
    connection = pyqtSignal(str)
    failed = pyqtSignal(str)
    stopped_all = pyqtSignal()
    result = pyqtSignal(dict)
    def request(self, *args):
        pass  # Captures never issue a command to a core or upstream service.
    def stop(self):
        pass
    def isRunning(self):
        return False


def capture(widget, filename, title, surface='native'):
    app.processEvents()
    time.sleep(.08)
    app.processEvents()
    assert widget.grab().save(str(OUT / filename)), filename
    SCREENS.append({'file': 'current/' + filename, 'title': title, 'surface': surface})


TIME = '2026-11-13T18:00:00+00:00'
DEVICES = [
    {'id': 'sample-phone-01', 'nickname': 'Personal phone', 'type': 'phone', 'manufacturer': 'Example', 'model': 'Phone One', 'active': True, 'can_push': True, 'can_sms': True},
    {'id': 'sample-desktop-01', 'nickname': 'Linux workstation', 'type': 'desktop', 'manufacturer': None, 'model': None, 'active': True, 'can_push': True, 'can_sms': False},
    {'id': 'sample-laptop-01', 'nickname': 'Work laptop', 'type': 'laptop', 'manufacturer': None, 'model': None, 'active': True, 'can_push': True, 'can_sms': False},
    {'id': 'sample-phone-02', 'nickname': 'Work phone', 'type': 'phone', 'manufacturer': 'Example', 'model': 'Phone Two', 'active': True, 'can_push': True, 'can_sms': True},
    {'id': 'sample-tablet-01', 'nickname': 'Retired tablet', 'type': 'tablet', 'manufacturer': None, 'model': None, 'active': False, 'can_push': False, 'can_sms': False},
]
ITEMS = [
    {'id': 1, 'source_id': 'sample:1', 'type': 'mirror', 'title': 'Messages — Project team', 'body': 'The release is ready for review. Let me know when you have a moment.\n\nFictional message for this UI review.', 'url': None, 'device_id': 'sample-phone-01', 'created_at': TIME},
    {'id': 2, 'source_id': 'sample:2', 'type': 'link', 'title': 'Design notes', 'body': 'A reference for our next discussion.', 'url': 'https://example.com/design-notes', 'device_id': 'sample-laptop-01', 'created_at': TIME},
    {'id': 3, 'source_id': 'sample:3', 'type': 'mirror', 'title': 'Calendar — Planning session', 'body': 'Your planning session starts in 15 minutes.', 'url': None, 'device_id': 'sample-phone-02', 'created_at': TIME},
    {'id': 4, 'source_id': 'sample:4', 'type': 'note', 'title': 'Weekend checklist', 'body': 'Pick up the parcel\nCharge the headphones\nBack up the laptop', 'url': None, 'device_id': 'sample-desktop-01', 'created_at': TIME},
]


def action(identifier=1, kind='sms', state='pending_approval', **changes):
    return dict(id=identifier, revision=1, fingerprint='a' * 64, kind=kind,
                transport='provider_plaintext', device_id='sample-phone-01' if kind == 'sms' else 'sample-desktop-01',
                recipient='+12025550123' if kind == 'sms' else None,
                title='' if kind == 'sms' else 'Planning notes', body='Can we move our meeting to 3:30? This is fictional sample text.',
                url='https://example.com/notes' if kind == 'link' else None,
                requested_by='desktop', state=state, created_at=TIME, updated_at=TIME,
                expires_at='2026-11-13T19:00:00+00:00', upstream_id=None, error_code=None, error_message=None,
                **changes)


CAPABILITIES = {'provider_state': 'connected', 'encryption_state': 'ready', 'encrypted_pending': 0,
                'send_ready': True, 'send_unavailable_reason': None,
                'features': {'inbox': True, 'search': True, 'devices': True, 'send': True,
                             'send_note': True, 'send_link': True, 'send_sms': True, 'e2e_decrypt': True}}
WEB = {'enabled': False, 'url': 'http://127.0.0.1:18768', 'host': '127.0.0.1', 'port': 18768, 'error': None}
ACTIONS = [action(7), action(6, 'link', 'upstream_accepted'), action(5, 'note', 'uncertain'),
           action(4, 'sms', 'failed'), action(3, 'note', 'cancelled')]
ACTIONS[1]['upstream_id'] = 'sample-acceptance-id'
ACTIONS[2].update(error_code='upstream_outcome_unknown', error_message='Provider response was ambiguous. No automatic retry.')
ACTIONS[3].update(error_code='device_unavailable', error_message='The selected device is no longer available for this channel.')

app = QApplication([])
app.setApplicationName('pbdesk UI review')
app.setQuitOnLastWindowClosed(False)
assert app.platformName() == 'wayland'
# Render the actual tray menu without registering an extra system-tray icon.
QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: True)
QSystemTrayIcon.show = lambda self: None
worker = ScreenshotWorker()
window = MainWindow(worker)
window.setWindowTitle('pbdesk — UI review (fictional data)')
window.show()


def snapshot(items=ITEMS, capabilities=CAPABILITIES):
    return {'items': items, 'web': WEB, 'capabilities': capabilities,
            'devices': {'devices': DEVICES, 'synced_at': TIME, 'provider_state': 'connected'},
            'actions': ACTIONS, 'instance_id': '00000000-0000-0000-0000-000000000001'}


worker.snapshot.emit(snapshot())
worker.connection.emit('Connected · local inbox')
window.tabs.setCurrentIndex(0)
capture(window, '01-inbox.png', 'Inbox and selected notification')
window.tabs.setCurrentIndex(1)
capture(window, '02-devices.png', 'Device list')
window.tabs.setCurrentIndex(2)
capture(window, '03-outbox.png', 'Outbox and action details')

for ordinal, kind in enumerate(('note', 'link', 'sms'), start=4):
    dialog = ComposeDialog(worker, DEVICES, window)
    dialog.kind.setCurrentIndex(dialog.kind.findData(kind))
    dialog.device.setCurrentIndex(dialog.device.findData('sample-phone-01' if kind == 'sms' else 'sample-desktop-01'))
    dialog.title.setText('Planning notes')
    dialog.body.setPlainText('Can we move our meeting to 3:30?\n\nFictional sample content for the design review.')
    if kind == 'sms':
        dialog.recipient.setText('+12025550123')
    if kind == 'link':
        dialog.url.setText('https://example.com/planning')
    dialog.show()
    capture(dialog, f'{ordinal:02d}-compose-{kind}.png', f'Compose {kind.title()}')
    dialog.reject()

review = ReviewDialog(worker, ACTIONS[0], DEVICES, CAPABILITIES, window)
review.show()
capture(review, '07-review-approval.png', 'Review and approve an exact message')
review.reject()
edit = ComposeDialog(worker, DEVICES, window, action=ACTIONS[0])
edit.show()
capture(edit, '08-edit-action.png', 'Edit an action')
edit.reject()


def capture_modal(filename, title):
    modal = app.activeModalWidget()
    assert modal is not None
    capture(modal, filename, title)
    if isinstance(modal, QMessageBox):
        modal.done(int(QMessageBox.StandardButton.No))
    else:
        modal.reject()


QTimer.singleShot(150, lambda: capture_modal('09-web-settings-disabled.png', 'Web hosting settings — disabled'))
window.show_settings()
window.web_state = dict(WEB, enabled=True)
QTimer.singleShot(150, lambda: capture_modal('10-web-settings-enabled.png', 'Web hosting settings — enabled'))
window.show_settings()
window.web_state = WEB
QTimer.singleShot(150, lambda: capture_modal('11-stop-all-confirmation.png', 'Stop all services confirmation'))
window.stop_all()
menu = window.menuBar().actions()[0].menu()
menu.popup(window.menuBar().mapToGlobal(window.menuBar().rect().bottomLeft()))
capture(menu, '12-application-menu.png', 'Application menu')
menu.hide()
tray_menu = window.tray.contextMenu()
tray_menu.popup(window.mapToGlobal(window.rect().center()))
capture(tray_menu, '13-tray-menu.png', 'Tray menu')
tray_menu.hide()
window.tabs.setCurrentIndex(0)
worker.snapshot.emit(snapshot(items=[]))
capture(window, '14-inbox-empty.png', 'Empty inbox')
worker.snapshot.emit(snapshot(capabilities=dict(CAPABILITIES, provider_state='reconnecting', encryption_state='locked', encrypted_pending=2, send_ready=False, send_unavailable_reason='Provider connection unavailable')))
window.show_error('Connection lost. The retained inbox remains available while the core reconnects.')
capture(window, '15-connection-error.png', 'Connection and locked-encryption state')
worker.snapshot.emit(snapshot())
uncertain = ReviewDialog(worker, ACTIONS[2], DEVICES, CAPABILITIES, window)
uncertain.show()
capture(uncertain, '16-review-uncertain.png', 'Review an uncertain send')
uncertain.reject()
window.exit_desktop()

# Capture the existing Jinja web UI against a local fake core response only.
import httpx
import uvicorn
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from pbdesk.config import Settings
from pbdesk.credentials import health_response
from pbdesk.web import app as web_module


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


with tempfile.TemporaryDirectory(prefix='pbdesk-ui-capture-') as temporary:
    settings = Settings(Path(temporary), core_port=free_port(), web_port=free_port())
    original_client = httpx.AsyncClient
    def respond(request):
        if request.url.path.endswith('/health'):
            return httpx.Response(200, json=health_response(settings, request.headers, '00000000-0000-0000-0000-000000000001', True))
        return httpx.Response(200, json={'provider_state': 'connected'})
    web_module.httpx.AsyncClient = lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(respond))
    web_app = web_module.create_app(settings)
    server = uvicorn.Server(uvicorn.Config(web_app, host='127.0.0.1', port=settings.web_port, access_log=False, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    assert server.started
    options = Options()
    options.binary_location = '/snap/firefox/current/usr/lib/firefox/firefox'
    options.add_argument('-headless')
    for key, value in {'network.proxy.type': 1, 'network.proxy.http': '127.0.0.1', 'network.proxy.http_port': 9,
                       'network.proxy.ssl': '127.0.0.1', 'network.proxy.ssl_port': 9,
                       'network.proxy.no_proxies_on': 'localhost, 127.0.0.1', 'app.update.auto': False,
                       'datareporting.healthreport.uploadEnabled': False, 'toolkit.telemetry.enabled': False}.items():
        options.set_preference(key, value)
    driver = None
    try:
        driver = webdriver.Firefox(service=Service(executable_path='/snap/firefox/current/usr/lib/firefox/geckodriver'), options=options)
        driver.set_page_load_timeout(15)
        driver.set_window_size(1440, 1000)
        driver.get(f'http://127.0.0.1:{settings.web_port}')
        assert driver.save_screenshot(str(OUT / '17-web-status-desktop.png'))
        SCREENS.append({'file': 'current/17-web-status-desktop.png', 'title': 'Web status — desktop', 'surface': 'web'})
        driver.set_window_size(390, 844)
        assert driver.save_screenshot(str(OUT / '18-web-status-mobile.png'))
        SCREENS.append({'file': 'current/18-web-status-mobile.png', 'title': 'Web status — mobile', 'surface': 'web'})
    finally:
        if driver:
            driver.quit()
        server.should_exit = True
        thread.join(8)
        assert not thread.is_alive()

(ROOT / 'capture-manifest.json').write_text(json.dumps({'data': 'fictional fixtures only', 'native_platform': 'wayland', 'screens': SCREENS}, indent=2) + '\n')
print(json.dumps({'captured_screens': len(SCREENS), 'folder': str(OUT), 'real_credentials_or_messages_read': False, 'live_core_touched': False}))
