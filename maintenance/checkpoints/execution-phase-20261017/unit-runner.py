"""Run trusted repository unit fixtures with a pre-import socket guard.

This guard is not a kernel sandbox or an execution grant.
"""
import ipaddress
import os
import socket
import sys
import tempfile

owned_git = tempfile.TemporaryDirectory(prefix="dip-sandbox-unit-git-")
for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS"):
    os.environ.pop(key, None)

os.environ.update({
    'OPENAI_API_KEY': 'test-only',
    'NEO4J_PASSWORD': 'test-only',
    'NEO4J_URI': 'bolt://127.0.0.1:1',
    'DISTILL_API_KEY': 'test-only',
    'LLM_API_KEY': 'test-only',
    'DISTILL_BASE_URL': 'http://127.0.0.1:1',
    'LLM_BASE_URL': 'http://127.0.0.1:1',
    'WIKI_MCP_EMBED_PROVIDER': 'openai',
    'WIKI_MCP_BACKGROUND_REINDEX': '0',
    'WIKI_ROOT': '/tmp/dip-sandbox-unit-fixture',
    'WIKI_REPO_URL': '',
    'WIKI_REPO_TOKEN': '',
    'CACHE_DIR': '',
    'MCP_METRICS_PATH': 'off',
    'NEO4J_INTEGRATION': '0',
    'RUN_PROJECTION_REPAIR_NATIVE': '0',
    'GIT_CONFIG_NOSYSTEM': '1',
    'GIT_CONFIG_SYSTEM': '/dev/null',
    'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_TEMPLATE_DIR': owned_git.name,
    'GIT_ALLOW_PROTOCOL': '',
    'GIT_PROTOCOL_FROM_USER': '0',
    'GIT_TERMINAL_PROMPT': '0',
    'GIT_ASKPASS': '/bin/false',
    'GIT_CONFIG_COUNT': '4',
    'GIT_CONFIG_KEY_0': 'credential.helper', 'GIT_CONFIG_VALUE_0': '',
    'GIT_CONFIG_KEY_1': 'protocol.allow', 'GIT_CONFIG_VALUE_1': 'never',
    'GIT_CONFIG_KEY_2': 'core.hooksPath', 'GIT_CONFIG_VALUE_2': owned_git.name,
    'GIT_CONFIG_KEY_3': 'init.templateDir', 'GIT_CONFIG_VALUE_3': owned_git.name,
})
blocked = {'dns': 0, 'connect': 0, 'connect_ex': 0, 'unix': 0, 'sendto': 0, 'sendmsg': 0}
blocked.update({'reverse_dns': 0, 'host_dns': 0, 'host_dns_ex': 0})
calls = dict(blocked)


def stop(kind):
    blocked[kind] += 1
    print('UNEXPECTED_SOCKET_ATTEMPT ' + kind + ' blocked_attempts=' + str(blocked), file=sys.stderr, flush=True)
    os._exit(97)


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode('ascii', errors='replace')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host) in {ipaddress.ip_address('127.0.0.1'), ipaddress.ip_address('::1')}
    except ValueError:
        return False


original_dns = socket.getaddrinfo
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_sendto = socket.socket.sendto
original_sendmsg = socket.socket.sendmsg


def guarded_dns(host, port, family=0, type=0, proto=0, flags=0):
    calls['dns'] += 1
    if not (local_host(host) or (host is None and flags & socket.AI_PASSIVE)):
        stop('dns')
    return original_dns(host, port, family, type, proto, flags)


def check_address(sock, address, kind='connect'):
    if sock.family == socket.AF_UNIX:
        calls['unix'] += 1
        stop('unix')
    if sock.family not in {socket.AF_INET, socket.AF_INET6} or not isinstance(address, tuple) or not local_host(address[0]):
        stop(kind)


def guarded_connect(sock, address):
    calls['connect'] += 1
    check_address(sock, address)
    return original_connect(sock, address)


def guarded_connect_ex(sock, address):
    calls['connect_ex'] += 1
    check_address(sock, address, 'connect_ex')
    return original_connect_ex(sock, address)


def guarded_sendto(sock, *args):
    calls['sendto'] += 1
    check_address(sock, args[-1], 'sendto')
    return original_sendto(sock, *args)


def guarded_sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
    calls['sendmsg'] += 1
    if address is None:
        try:
            address = sock.getpeername()
        except OSError:
            stop('sendmsg')
    check_address(sock, address, 'sendmsg')
    return original_sendmsg(sock, buffers, ancdata, flags, address)


original_reverse_dns = socket.gethostbyaddr
original_host_dns = socket.gethostbyname
original_host_dns_ex = socket.gethostbyname_ex


def guarded_reverse_dns(host):
    calls['reverse_dns'] += 1
    if not local_host(host):
        stop('reverse_dns')
    return original_reverse_dns(host)


def guarded_host_dns(host):
    calls['host_dns'] += 1
    if not local_host(host):
        stop('host_dns')
    return original_host_dns(host)


def guarded_host_dns_ex(host):
    calls['host_dns_ex'] += 1
    if not local_host(host):
        stop('host_dns_ex')
    return original_host_dns_ex(host)


socket.gethostbyaddr = guarded_reverse_dns
socket.gethostbyname = guarded_host_dns
socket.gethostbyname_ex = guarded_host_dns_ex
socket.socket.sendto = guarded_sendto
socket.socket.sendmsg = guarded_sendmsg
socket.getaddrinfo = guarded_dns
socket.socket.connect = guarded_connect
socket.socket.connect_ex = guarded_connect_ex
print('Pre-import loopback-only socket guard installed; blocked_attempts=' + str(blocked), flush=True)

import unittest

root = os.getcwd()
sys.path.insert(0, root + '/server')
sys.path.insert(0, root + '/server/tests')
sys.path.insert(0, root + '/maintenance/checkpoints/execution-phase-20261017')
sys.path.append('/tmp')
mode = sys.argv[1]
loader = unittest.defaultTestLoader
if mode == 'focused':
    suite = loader.discover(root + '/server/tests', pattern='test_*sandbox*.py')
elif mode == 'full':
    suite = loader.discover(root + '/server/tests')
else:
    groups = {
        'phase': ['test_execution_phase_qualifiers', 'test_graph_execution_phases'],
        'independent14': ['independent_phase_review'],
        'independent14': ['test_execution_phase_bundle_f1c_independent'],
        'independent': ['test_execution_phase_bundle_independent'],
        'phasehelper': ['test_execution_phase_qualifiers'],
        'storage68': ['test_storage_authority_qualifiers', 'test_graph_storage_authority'],
        'old314': ['test_resource_scope_qualifiers', 'test_graph_resource_scopes', 'test_resource_query_roles',
                   'test_graph_resource_query_roles', 'test_network_vantage_qualifiers', 'test_graph_network_vantage',
                   'test_release_scope_qualifiers', 'test_graph_release_scopes'],
        'queryroles34': ['test_resource_query_roles', 'test_graph_resource_query_roles'],
        'http6': ['test_http_responsiveness'],
    }
    suite = loader.loadTestsFromNames(groups[mode])
result = unittest.TextTestRunner(verbosity=1).run(suite)
print('Final blocked_attempts=' + str(blocked), flush=True)
print('Final hooked_calls=' + str(calls), flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
