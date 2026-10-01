#!/usr/bin/env python3
"""Exercise the real local server's OAuth TLS path using an invalid code.

Run only against a disposable, unauthenticated guest with the official server
already listening. This starts/cancels an OAuth login; it cannot sign in a user.
Only fixed result labels are printed, never server payloads or OAuth URLs.
"""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import urllib.parse
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('diagnostic', str(PROJECT / 'runtime/usr/local/libexec/codexpad/diagnose'))
spec = importlib.util.spec_from_loader(loader.name, loader)
diagnostic = importlib.util.module_from_spec(spec)
loader.exec_module(diagnostic)


def callback_url(auth_url):
    # Never let a server-provided redirect send this probe to another host.
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query)
    redirect = query['redirect_uri'][0]
    parsed = urllib.parse.urlsplit(redirect)
    if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') or
            parsed.port != 1455 or parsed.path != '/auth/callback' or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise diagnostic.ProbeFailure('unexpected-callback')
    return redirect + '?' + urllib.parse.urlencode(dict(state=query['state'][0], code='codexpad-invalid-regression-code'))


def probe():
    stage = 'transport.websocket'
    with diagnostic.websocket() as connection:
        login_id = None
        try:
            diagnostic.emit(stage, 'ok')
            stage = 'protocol.initialize'
            diagnostic.request(connection, 1, 'initialize', dict(clientInfo=dict(name='codexpad-oauth-probe', version='1'),
                                                               capabilities=dict(experimentalApi=True)))
            diagnostic.send_frame(connection, b'{"method":"initialized"}')
            diagnostic.emit(stage, 'ok')
            stage = 'runtime.manifest'
            result = diagnostic.request(connection, 2, 'fs/readFile', dict(path='/usr/local/share/codexpad/runtime.json'))
            manifest = json.loads(diagnostic.base64.b64decode(result['dataBase64']))
            revision = json.loads((PROJECT / 'Dependencies/upstreams.json').read_text())['codex']['revision']
            if manifest.get('codexRevision') != revision or manifest.get('hasAppServer') is not True:
                raise diagnostic.ProbeFailure('pin-mismatch')
            diagnostic.emit(stage, 'ok')
            stage = 'authentication.account.read'
            result = diagnostic.request(connection, 3, 'account/read', dict(refreshToken=False))
            if 'account' not in result or result['account'] is not None:
                raise diagnostic.ProbeFailure('requires-disposable-unauthenticated-guest')
            diagnostic.emit(stage, 'ok')
            stage = 'authentication.login.start'
            result = diagnostic.request(connection, 4, 'account/login/start',
                                        dict(type='chatgpt', useHostedLoginSuccessPage=True, appBrand='codex'))
            login_id = result['loginId']
            callback = callback_url(result['authUrl'])
            diagnostic.emit(stage, 'ok')
            stage = 'authentication.token.exchange'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(callback, timeout=60) as response:
                body = response.read(1024 * 1024)
            # Local callback's HTTP 200 is an error page. Require the upstream
            # token endpoint's actual 401, proving verified HTTPS reached HTTP.
            if b'401 Unauthorized' not in body:
                raise diagnostic.ProbeFailure('no-token-http-response')
            diagnostic.emit(stage, 'expected-invalid-code-rejection', token_http_status=401)
        except Exception as error:
            diagnostic.emit(stage, diagnostic.failure(error))
            return False
        finally:
            if login_id:
                try:
                    diagnostic.request(connection, 5, 'account/login/cancel', dict(loginId=login_id))
                except Exception:
                    pass
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disposable-guest', action='store_true', required=True,
                        help='confirm this probe may start/cancel a login in this isolated guest')
    parser.parse_args()
    try:
        success = probe()
    except Exception as error:
        diagnostic.emit('transport.websocket', diagnostic.failure(error))
        success = False
    raise SystemExit(0 if success else 1)
