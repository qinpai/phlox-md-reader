#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安装可选依赖 epam.indigo 到 vendor/，供化学结构式/反应式渲染使用。

用法：
    python install_indigo.py           # 装到 ./vendor
    python install_indigo.py --check   # 只检查，不安装

装不上也没关系：mdread.py 只会在 ``vendor/`` 里找不到 indigo 时跳过化学块，
其它功能（目录、插图、公式、导出）不受影响。
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")


def probe():
    """尝试导入 indigo 并渲染一个结构式，返回 (ok, 说明)。"""
    sys.path.insert(0, VENDOR)
    try:
        from indigo import Indigo
        from indigo.renderer import IndigoRenderer
    except Exception as exc:  # noqa: BLE001
        return False, "未安装：%s" % exc

    try:
        ind = Indigo()
        # 渲染相关的选项（render-output-format / render-image-size …）由渲染器注册，
        # 必须先构造 IndigoRenderer，再 setOption，否则报 Property ... not defined。
        renderer = IndigoRenderer(ind)
        ind.setOption("render-output-format", "png")
        ind.setOption("render-image-size", "300,200")
        mol = ind.loadMolecule("CC(=O)Oc1ccccc1C(=O)O")  # 阿司匹林
        buf = renderer.renderToBuffer(mol)
        return True, "可用（测试渲染 %d 字节）" % len(buf)
    except Exception as exc:  # noqa: BLE001
        return False, "已安装但渲染失败：%s" % exc


def main():
    ap = argparse.ArgumentParser(description="安装化学渲染可选依赖 epam.indigo")
    ap.add_argument("--check", action="store_true", help="只检查当前状态，不安装")
    args = ap.parse_args()

    ok, msg = probe()
    print("[检查] vendor/indigo：%s" % msg)
    if ok or args.check:
        return 0 if ok else 1

    print("[安装] pip install --target vendor epam.indigo")
    os.makedirs(VENDOR, exist_ok=True)
    cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir",
           "--target", VENDOR, "epam.indigo"]
    rc = subprocess.call(cmd)
    if rc != 0:
        print("\n[失败] pip 返回 %d。" % rc)
        print("常见原因：没联网、pip 临时目录被安全软件拦截、Python 版本过新没有轮子。")
        print("可以先继续用除化学图以外的所有功能；需要时可重跑本脚本。")
        return rc

    ok, msg = probe()
    print("[检查] 安装后：%s" % msg)
    if ok:
        print("\n完成。运行 python mdread.py doctor 可再次确认。")
        return 0
    print("\n安装了但验证未通过，请把上面的报错贴到 issue。")
    return 1


if __name__ == "__main__":
    sys.exit(main())