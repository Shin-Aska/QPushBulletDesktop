"""Domain-separated proof of the private optional web process identity."""
import re
import secrets
from pbdesk.credentials import service_token, _health_mac


def web_health_headers(settings):
    nonce = secrets.token_hex(32)
    proof = _health_mac(service_token(settings, 'web'), 'pbdesk-web-health-request-v1\n' + nonce)
    return {'X-PBDesk-Web-Nonce': nonce, 'X-PBDesk-Web-Proof': proof}


def web_health_response(settings, headers, instance_id, generation):
    nonce = headers.get('x-pbdesk-web-nonce', headers.get('X-PBDesk-Web-Nonce', ''))
    proof = headers.get('x-pbdesk-web-proof', headers.get('X-PBDesk-Web-Proof', ''))
    token = service_token(settings, 'web')
    if not re.fullmatch('[0-9a-f]{64}', nonce) or not secrets.compare_digest(proof, _health_mac(token, 'pbdesk-web-health-request-v1\n' + nonce)):
        return None
    return {'application': 'pbdesk-web', 'api_version': 1, 'instance_id': instance_id, 'generation': generation,
            'proof': _health_mac(token, 'pbdesk-web-health-response-v1\n' + nonce + '\n' + instance_id + '\n' + generation)}


def verify_web_health(settings, headers, body):
    if not isinstance(body, dict) or body.get('application') != 'pbdesk-web' or type(body.get('api_version')) is not int or body['api_version'] != 1:
        return False
    instance, generation = body.get('instance_id'), body.get('generation')
    if not all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in (instance, generation)):
        return False
    nonce = headers.get('X-PBDesk-Web-Nonce', '')
    expected = _health_mac(service_token(settings, 'web'), 'pbdesk-web-health-response-v1\n' + nonce + '\n' + instance + '\n' + generation)
    return isinstance(body.get('proof'), str) and secrets.compare_digest(body['proof'], expected)
