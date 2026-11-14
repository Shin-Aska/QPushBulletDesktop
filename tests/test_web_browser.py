"""Opt-in real Firefox smoke; uses installed binaries, never Selenium Manager."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.environ.get("PBDESK_BROWSER_TEST") != "1", reason="Set PBDESK_BROWSER_TEST=1 to use existing Firefox/geckodriver")
def test_firefox_local_bootstrap_with_external_requests_blocked(tmp_path):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    firefox, gecko = shutil.which("firefox"), shutil.which("geckodriver")
    # Ubuntu ships launcher shims; select the already installed actual binaries.
    snap_root = Path("/snap/firefox/current/usr/lib/firefox")
    if (snap_root / "firefox").is_file() and firefox == "/usr/bin/firefox":
        firefox = str(snap_root / "firefox")
    if (snap_root / "geckodriver").is_file() and gecko == "/snap/bin/geckodriver":
        gecko = str(snap_root / "geckodriver")
    if not firefox or not gecko:
        pytest.skip("Existing Firefox and geckodriver are required; no downloads are allowed")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    core_port = port - 1 if port > 1025 else port + 1
    env = dict(os.environ, PBDESK_STATE_DIR=str(tmp_path / "state"), PBDESK_APPLICATION_DIR=str(tmp_path / "application"), PBDESK_WEB_PORT=str(port), PBDESK_CORE_PORT=str(core_port))
    process = subprocess.Popen([sys.executable, "-m", "pbdesk.web", "--standalone"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}"
    driver = None
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/static/css/app.css", timeout=0.2) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise AssertionError("Web host did not start")
        options = Options()
        options.binary_location = firefox
        options.add_argument("-headless")
        # A dead proxy blocks all external HTTP(S); only loopback bypasses it.
        for key, value in {"network.proxy.type": 1, "network.proxy.http": "127.0.0.1", "network.proxy.http_port": 9, "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": 9, "network.proxy.no_proxies_on": "localhost, 127.0.0.1", "app.update.auto": False, "datareporting.healthreport.uploadEnabled": False, "toolkit.telemetry.enabled": False}.items():
            options.set_preference(key, value)
        driver = webdriver.Firefox(service=Service(executable_path=gecko), options=options)
        driver.set_page_load_timeout(15)
        driver.get(url)
        assert driver.find_element("tag name", "h1").text == "Service status"
        assert driver.find_element("id", "core-status").text == "Unavailable"
        resources = driver.execute_script("return performance.getEntriesByType('resource').map(e => e.name)")
        assert any("bootstrap.min.css" in value for value in resources)
        assert any("bootstrap.bundle.min.js" in value for value in resources)
        assert all(value.startswith(url + "/") for value in resources)
        assert driver.execute_script("return getComputedStyle(document.querySelector('.card')).borderRadius") == "8px"
        driver.set_window_size(390, 844)
        assert driver.execute_script("return document.documentElement.scrollWidth <= innerWidth")
        driver.find_element("id", "refresh-status").click()
        assert "pbdesk" in driver.title
    finally:
        if driver:
            driver.quit()
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
