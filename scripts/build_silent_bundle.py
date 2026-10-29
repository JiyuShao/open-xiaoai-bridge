"""打包静默验收所需源码和公开测试配置，排除模型、凭据及运行数据。"""
import argparse
from pathlib import Path
import tarfile
from core.utils.logger import logger

SOURCES=('core','scripts','tests','skills','profiles','native/src','native/Cargo.toml',
         'config.py','config.doubao-test.py','config.mixed-wake.py','config.native-wake.py',
         'main.py','pyproject.toml','Dockerfile','README.md','docker-compose.doubao-validation.yml')


def build(output):
    root=Path(__file__).resolve().parents[1]
    with tarfile.open(output,'w:gz') as archive:
        for name in SOURCES:
            path=root/name
            candidates=sorted(path.rglob('*')) if path.is_dir() else [path]
            for item in candidates:
                relative=item.relative_to(root)
                if item.is_file() and not item.is_symlink() and not any(part in ('__pycache__','models','.env','.git') for part in relative.parts) and item.suffix!='.pyc':
                    archive.add(item,arcname=str(relative),recursive=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build(args.output)
    logger.info(f'静默源码包已生成：{args.output}',module='SilentBundle')
