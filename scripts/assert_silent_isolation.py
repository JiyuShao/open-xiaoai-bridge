"""在专用无网络测试容器内证明没有生产网络、宿主机控制及生产秘密。"""

import json
import os
from pathlib import Path
import socket

from core.utils.logger import logger


def check():
    if not Path('/.dockerenv').exists() or os.getuid() == 0:
        raise RuntimeError('必须在非root专用测试容器中运行')
    interfaces = sorted(path.name for path in Path('/sys/class/net').iterdir() if path.is_dir())
    flags = {name:int((Path('/sys/class/net')/name/'flags').read_text().strip(),16) for name in interfaces}
    if 'lo' not in flags or any(value & 1 for name,value in flags.items() if name != 'lo'):
        raise RuntimeError('测试容器不得有启用的非loopback接口')
    routes = Path('/proc/net/route').read_text().splitlines()[1:]
    if any(line.split()[0] != 'lo' for line in routes if line.strip()):
        raise RuntimeError('测试容器不得有非loopback IPv4路由')
    if any(line.split()[-1] != 'lo' for line in Path('/proc/net/ipv6_route').read_text().splitlines() if line.strip()):
        raise RuntimeError('测试容器不得有非loopback IPv6路由')
    if Path('/var/run/docker.sock').exists():
        raise RuntimeError('不得挂载Docker控制接口')
    capabilities = next(line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))
    if int(capabilities,16):
        raise RuntimeError('测试容器必须丢弃全部capabilities')
    forbidden = [key for key in os.environ if os.environ[key] and any(part in key for part in ('TOKEN','SECRET','PASSWORD','API_KEY','ACCESS_KEY'))]
    if forbidden:
        raise RuntimeError('测试容器不得继承凭据环境变量')
    blocked = []
    # 无路由时不会向这些地址发包；只证明测试进程无法连接。
    for host,port in [('192.168.1.2',4399),('192.168.1.2',9092),('192.168.1.2',8123),('1.1.1.1',443)]:
        with socket.socket() as connection:
            connection.settimeout(0.2)
            code = connection.connect_ex((host,port))
            if code == 0:
                raise RuntimeError('隔离失败，测试进程可达外部网络')
            blocked.append({'host':host,'port':port,'errno':code})
    result={'interfaces':interfaces,'interface_flags':flags,'uid':os.getuid(),'capabilities':capabilities,
            'docker_socket':False,'credential_variables':False,'unreachable':blocked}
    logger.info(json.dumps(result),module='SilentIsolation')
    return result


if __name__ == '__main__':
    check()
